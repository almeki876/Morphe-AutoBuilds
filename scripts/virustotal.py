"""Scan every release APK with VirusTotal before publishing it.

The scanner is intentionally fail-closed: a missing API key, an incomplete
analysis, a VirusTotal API failure, or an unreviewed malicious/suspicious
detection prevents the release job from continuing.
"""

from __future__ import annotations

import hashlib
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

from curl_cffi import CurlMime, requests


API_BASE = "https://www.virustotal.com/api/v3"
SMALL_UPLOAD_LIMIT = 32 * 1024 * 1024
MAX_UPLOAD_SIZE = 650 * 1024 * 1024
RETRYABLE_STATUS_CODES = {408, 424, 429, 500, 502, 503, 504}
LOW_CONFIDENCE_ENGINES = {
    "MaxSecure",
    "Gridinsoft",
}
MAJOR_ENGINES = {
    "Google",
    "Microsoft",
    "Kaspersky",
    "ESET-NOD32",
    "BitDefender",
    "Sophos",
}


class VirusTotalError(RuntimeError):
    """Raised when an APK cannot be conclusively scanned."""


class VirusTotalAnalysisTimeout(VirusTotalError):
    """Raised when a queued VirusTotal analysis does not finish in time."""


@dataclass(frozen=True)
class ScanResult:
    file: str
    sha256: str
    size: int
    analysis_id: str
    malicious: int
    suspicious: int
    harmless: int
    undetected: int
    timeout: int
    failure: int
    unsupported: int
    verdict: str
    method: str
    engines: dict[str, dict[str, object]]
    permalink: str
    last_analysis_date: int | None
    reanalyzed: bool


@dataclass(frozen=True)
class HashLookup:
    sha256: str
    analysis_id: str | None
    stats: dict[str, int]
    engines: dict[str, dict[str, object]]
    method: str
    last_analysis_date: int | None
    reanalyzed: bool


class VirusTotalClient:
    def __init__(
        self,
        api_key: str,
        *,
        request_interval: float,
        poll_interval: float,
        analysis_timeout: float,
        max_retries: int,
        max_analysis_age_days: float = 90,
    ) -> None:
        self.session = requests.Session()
        self.headers = {"x-apikey": api_key, "accept": "application/json"}
        self.request_interval = request_interval
        self.poll_interval = poll_interval
        self.analysis_timeout = analysis_timeout
        self.max_retries = max_retries
        self.max_analysis_age_seconds = max(0.0, max_analysis_age_days) * 86400
        self._last_request = 0.0
        self._rate_limit_lock = threading.Lock()

    def _rate_limit(self) -> None:
        with self._rate_limit_lock:
            delay = self.request_interval - (time.monotonic() - self._last_request)
            if delay > 0:
                time.sleep(delay)
            self._last_request = time.monotonic()

    @staticmethod
    def _error_message(response: requests.Response) -> str:
        try:
            error = response.json().get("error", {})
            code = error.get("code", f"HTTP {response.status_code}")
            message = error.get("message", response.text[:300])
            return f"{code}: {message}"
        except (ValueError, AttributeError):
            return f"HTTP {response.status_code}: {response.text[:300]}"

    def request(
        self,
        method: str,
        url: str,
        *,
        expected: tuple[int, ...] = (200,),
        file_path: Path | None = None,
    ) -> requests.Response:
        last_error = "unknown error"
        for attempt in range(self.max_retries + 1):
            self._rate_limit()
            response: requests.Response | None = None
            multipart: CurlMime | None = None
            try:
                kwargs = {
                    "headers": self.headers,
                    "timeout": 180,
                }
                if file_path is not None:
                    # curl_cffi intentionally does not implement requests'
                    # ``files=`` shortcut. Build a native libcurl MIME form
                    # for every attempt so retries start from a fresh file.
                    multipart = CurlMime()
                    multipart.addpart(
                        "file",
                        filename=file_path.name,
                        content_type=(
                            "application/vnd.android.package-archive"
                            if file_path.suffix.casefold() == ".apk"
                            else "application/octet-stream"
                        ),
                        local_path=file_path,
                    )
                    kwargs["multipart"] = multipart
                response = self.session.request(method, url, **kwargs)
            except requests.RequestsError as error:
                last_error = f"{type(error).__name__}: {error}"
                retryable = True
            else:
                if response.status_code in expected:
                    return response
                last_error = self._error_message(response)
                retryable = response.status_code in RETRYABLE_STATUS_CODES
            finally:
                if multipart is not None:
                    multipart.close()

            if not retryable or attempt >= self.max_retries:
                raise VirusTotalError(f"{method} {url} failed: {last_error}")

            retry_after = 0.0
            if response is not None:
                try:
                    retry_after = float(response.headers.get("retry-after", 0))
                except (TypeError, ValueError):
                    retry_after = 0.0
            delay = max(retry_after, min(120.0, 15.0 * (2**attempt)))
            print(
                f"::warning::VirusTotal request failed ({last_error}); "
                f"retrying in {delay:.0f}s",
                flush=True,
            )
            time.sleep(delay)

        raise VirusTotalError(f"{method} {url} failed: {last_error}")

    def _start_upload(self, path: Path) -> str:
        if path.stat().st_size > MAX_UPLOAD_SIZE:
            raise VirusTotalError(
                f"{path.name} is larger than VirusTotal's 650 MiB upload limit"
            )

        upload_url = f"{API_BASE}/files"
        if path.stat().st_size > SMALL_UPLOAD_LIMIT:
            response = self.request("GET", f"{API_BASE}/files/upload_url")
            upload_url = str(response.json().get("data") or "")
            if not upload_url.startswith(("https://", "http://")):
                raise VirusTotalError("VirusTotal returned an invalid upload URL")

        print(f"Uploading {path.name} to VirusTotal...", flush=True)
        response = self.request("POST", upload_url, file_path=path)
        analysis_id = str(response.json().get("data", {}).get("id") or "")
        if not analysis_id:
            raise VirusTotalError(
                f"VirusTotal did not return an analysis ID for {path.name}"
            )
        return analysis_id

    @staticmethod
    def _stats(attributes: dict) -> dict[str, int]:
        raw_stats = attributes.get("last_analysis_stats")
        if not isinstance(raw_stats, dict):
            return {}
        return {
            str(key): int(value)
            for key, value in raw_stats.items()
            if isinstance(value, (int, float))
        }

    @staticmethod
    def _engine_results(
        attributes: dict,
        key: str,
    ) -> dict[str, dict[str, object]]:
        raw_results = attributes.get(key)
        if not isinstance(raw_results, dict):
            return {}
        results: dict[str, dict[str, object]] = {}
        for engine, raw in raw_results.items():
            if not isinstance(raw, dict):
                continue
            results[str(engine)] = {
                str(field): value
                for field, value in raw.items()
                if isinstance(value, (str, int, float, bool)) or value is None
            }
        return results

    def lookup_hash(self, path: Path, sha256: str) -> HashLookup:
        report_url = f"{API_BASE}/files/{quote(sha256)}"
        response = self.request("GET", report_url, expected=(200, 404))

        if response.status_code == 404:
            return HashLookup(
                sha256, None, {}, {}, "uploaded", None, False
            )

        attributes = response.json().get("data", {}).get("attributes", {})
        stats = self._stats(attributes)
        if not stats:
            print(f"Requesting a fresh analysis for {path.name}...", flush=True)
            response = self.request("POST", f"{report_url}/analyse")
            analysis_id = str(response.json().get("data", {}).get("id") or "")
            if not analysis_id:
                raise VirusTotalError(
                    f"VirusTotal did not return an analysis ID for {path.name}"
                )
            return HashLookup(sha256, analysis_id, {}, {}, "reanalyzed", None, True)

        engines = self._engine_results(attributes, "last_analysis_results")
        raw_date = attributes.get("last_analysis_date")
        last_analysis_date = (
            int(raw_date) if isinstance(raw_date, (int, float)) else None
        )
        malware_detected = stats.get("malicious", 0) > 0
        is_stale = malware_detected and (
            last_analysis_date is None
            or time.time() - last_analysis_date > self.max_analysis_age_seconds
        )
        if is_stale:
            print(
                f"Existing VirusTotal result for {path.name} is older than "
                f"{self.max_analysis_age_seconds / 86400:.0f} days; "
                "requesting a fresh analysis.",
                flush=True,
            )
            response = self.request("POST", f"{report_url}/analyse")
            analysis_id = str(response.json().get("data", {}).get("id") or "")
            if not analysis_id:
                raise VirusTotalError(
                    f"VirusTotal did not return an analysis ID for {path.name}"
                )
            return HashLookup(
                sha256, analysis_id, stats, engines, "reanalyzed", last_analysis_date, True
            )

        print(
            f"Using existing VirusTotal result for {path.name} "
            f"(SHA-256 {sha256[:12]}...); no upload required.",
            flush=True,
        )
        return HashLookup(
            sha256, None, stats, engines, "hash lookup", last_analysis_date, False
        )

    def start_analysis(
        self,
        path: Path,
        sha256: str,
    ) -> tuple[
        str | None,
        dict[str, int],
        dict[str, dict[str, object]],
        str,
        int | None,
        bool,
    ]:
        lookup = self.lookup_hash(path, sha256)
        if lookup.analysis_id is not None or lookup.method != "uploaded":
            return (
                lookup.analysis_id,
                lookup.stats,
                lookup.engines,
                lookup.method,
                lookup.last_analysis_date,
                lookup.reanalyzed,
            )
        return self._start_upload(path), {}, {}, "uploaded", None, False

    def wait_for_analysis(
        self,
        analysis_id: str,
        filename: str,
    ) -> tuple[dict[str, int], dict[str, dict[str, object]], int | None]:
        deadline = time.monotonic() + self.analysis_timeout
        encoded_id = quote(analysis_id, safe="")
        while time.monotonic() < deadline:
            response = self.request("GET", f"{API_BASE}/analyses/{encoded_id}")
            attributes = response.json().get("data", {}).get("attributes", {})
            status = attributes.get("status")
            if status == "completed":
                raw_stats = attributes.get("stats")
                if not isinstance(raw_stats, dict):
                    raise VirusTotalError(
                        f"Completed analysis has no statistics for {filename}"
                    )
                stats = {
                    str(key): int(value)
                    for key, value in raw_stats.items()
                    if isinstance(value, (int, float))
                }
                engines = self._engine_results(attributes, "results")
                raw_date = attributes.get("date")
                completed_date = (
                    int(raw_date) if isinstance(raw_date, (int, float)) else None
                )
                return stats, engines, completed_date
            if status not in {"queued", "in-progress"}:
                raise VirusTotalError(
                    f"Unexpected VirusTotal analysis status for {filename}: {status!r}"
                )
            print(
                f"VirusTotal analysis for {filename}: {status}; waiting...",
                flush=True,
            )
            time.sleep(self.poll_interval)
        raise VirusTotalAnalysisTimeout(
            f"VirusTotal analysis timed out after "
            f"{self.analysis_timeout / 60:.0f} minutes for {filename}"
        )

    def scan(self, path: Path) -> ScanResult:
        sha256 = sha256_file(path)
        return self.scan_lookup(path, self.lookup_hash(path, sha256))

    def scan_lookup(self, path: Path, lookup: HashLookup) -> ScanResult:
        analysis_id = lookup.analysis_id
        stats = lookup.stats
        engines = lookup.engines
        method = lookup.method
        last_analysis_date = lookup.last_analysis_date
        reanalyzed = lookup.reanalyzed
        if method == "uploaded":
            analysis_id = self._start_upload(path)
        if analysis_id:
            try:
                stats, engines, completed_date = self.wait_for_analysis(
                    analysis_id, path.name
                )
                last_analysis_date = completed_date or int(time.time())
            except VirusTotalAnalysisTimeout as error:
                if method == "reanalyzed" and stats:
                    print(
                        f"::warning::VirusTotal analysis for {path.name} is still "
                        f"in progress; using the previous result temporarily: {error}",
                        flush=True,
                    )
                    method = "stale fallback"
                    reanalyzed = False
                else:
                    raise
        malicious = stats.get("malicious", 0)
        suspicious = stats.get("suspicious", 0)
        completed_engines = (
            malicious
            + suspicious
            + stats.get("harmless", 0)
            + stats.get("undetected", 0)
        )
        if completed_engines == 0:
            raise VirusTotalError(
                f"VirusTotal returned no completed engine verdicts for {path.name}"
            )
        detected_engines = {
            engine
            for engine, details in engines.items()
            if details.get("category") in {"malicious", "suspicious"}
        }
        only_low_confidence_malicious = (
            malicious == 1
            and suspicious == 0
            and len(detected_engines) == 1
            and next(iter(detected_engines)) in LOW_CONFIDENCE_ENGINES
        )
        if only_low_confidence_malicious:
            print(
                f"::warning::{path.name}: single low-confidence VirusTotal "
                f"detection ({next(iter(detected_engines))}); allowing release.",
                flush=True,
            )
        major_detection = any(
            engine in MAJOR_ENGINES
            for engine in detected_engines
            if engines[engine].get("category") == "malicious"
        )
        verdict = (
            "clean"
            if suspicious == 0 and (malicious == 0 or only_low_confidence_malicious)
            else "unsafe"
        )
        if major_detection:
            verdict = "unsafe"
        return ScanResult(
            file=path.name,
            sha256=lookup.sha256,
            size=path.stat().st_size,
            analysis_id=analysis_id or "",
            malicious=malicious,
            suspicious=suspicious,
            harmless=stats.get("harmless", 0),
            undetected=stats.get("undetected", 0),
            timeout=stats.get("timeout", 0)
            + stats.get("confirmed-timeout", 0),
            failure=stats.get("failure", 0),
            unsupported=stats.get("type-unsupported", 0),
            verdict=verdict,
            method=method,
            engines=engines,
            permalink=f"https://www.virustotal.com/gui/file/{lookup.sha256}/detection",
            last_analysis_date=last_analysis_date,
            reanalyzed=reanalyzed,
        )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def markdown_report(results: list[ScanResult]) -> str:
    lines = [
        "",
        "## VirusTotal scan results",
        "",
        (
            "Each file was checked by SHA-256 first and uploaded only when VirusTotal "
            "had no recent existing result. Suspicious detections and confirmed "
            "malicious detections block the release."
        ),
        "",
        "| APK | SHA-256 | Method | Last analysis (UTC) | Reanalyzed | Malicious | Suspicious | Undetected | Result |",
        "| --- | --- | --- | --- | --- | ---: | ---: | ---: | --- |",
    ]
    for result in results:
        if result.verdict == "clean":
            label = "No detections"
        else:
            label = "Blocked"
        escaped_file = result.file.replace("|", r"\|")
        lines.append(
            f"| {escaped_file} | "
            f"[`{result.sha256[:12]}…`]({result.permalink}) | "
            f"{result.method} | "
            f"{time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(result.last_analysis_date)) if result.last_analysis_date else 'unknown'} | "
            f"{str(result.reanalyzed).lower()} | "
            f"{result.malicious} | {result.suspicious} | "
            f"{result.undetected} | {label} |"
        )
    detected_results = [
        result
        for result in results
        if any(
            details.get("category") in {"malicious", "suspicious"}
            for details in result.engines.values()
        )
    ]
    for result in detected_results:
        escaped_file = result.file.replace("|", r"\|")
        lines.extend(
            [
                "",
                f"### Detection details: {escaped_file}",
                "",
                "| Engine | Category | Detection | Method | Engine version | Update |",
                "| --- | --- | --- | --- | --- | --- |",
            ]
        )
        for engine, details in sorted(result.engines.items()):
            category = str(details.get("category") or "")
            if category not in {"malicious", "suspicious"}:
                continue
            values = [
                engine,
                category,
                str(details.get("result") or ""),
                str(details.get("method") or ""),
                str(details.get("engine_version") or ""),
                str(details.get("engine_update") or ""),
            ]
            escaped = [value.replace("|", r"\|") for value in values]
            lines.append("| " + " | ".join(escaped) + " |")
    return "\n".join(lines) + "\n"
