"""Scan every release APK with VirusTotal before publishing it.

The scanner is intentionally fail-closed: a missing API key, an incomplete
analysis, a VirusTotal API failure, or any malicious/suspicious detection
prevents the release job from continuing.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

from curl_cffi import CurlMime, requests


API_BASE = "https://www.virustotal.com/api/v3"
SMALL_UPLOAD_LIMIT = 32 * 1024 * 1024
MAX_UPLOAD_SIZE = 650 * 1024 * 1024
RETRYABLE_STATUS_CODES = {408, 424, 429, 500, 502, 503, 504}


class VirusTotalError(RuntimeError):
    """Raised when an APK cannot be conclusively scanned."""


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
    permalink: str


class VirusTotalClient:
    def __init__(
        self,
        api_key: str,
        *,
        request_interval: float,
        poll_interval: float,
        analysis_timeout: float,
        max_retries: int,
    ) -> None:
        self.session = requests.Session()
        self.headers = {"x-apikey": api_key, "accept": "application/json"}
        self.request_interval = request_interval
        self.poll_interval = poll_interval
        self.analysis_timeout = analysis_timeout
        self.max_retries = max_retries
        self._last_request = 0.0

    def _rate_limit(self) -> None:
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
                        content_type="application/vnd.android.package-archive",
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

    def start_analysis(self, path: Path, sha256: str) -> str:
        report_url = f"{API_BASE}/files/{quote(sha256)}"
        response = self.request("GET", report_url, expected=(200, 404))

        if response.status_code == 200:
            print(f"Requesting a fresh analysis for {path.name}...", flush=True)
            response = self.request("POST", f"{report_url}/analyse")
            analysis_id = str(response.json().get("data", {}).get("id") or "")
            if not analysis_id:
                raise VirusTotalError(
                    f"VirusTotal did not return an analysis ID for {path.name}"
                )
            return analysis_id
        if response.status_code != 404:
            raise VirusTotalError(
                f"Could not check {path.name}: {self._error_message(response)}"
            )
        return self._start_upload(path)

    def wait_for_analysis(self, analysis_id: str, filename: str) -> dict[str, int]:
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
                return {
                    str(key): int(value)
                    for key, value in raw_stats.items()
                    if isinstance(value, (int, float))
                }
            if status not in {"queued", "in-progress"}:
                raise VirusTotalError(
                    f"Unexpected VirusTotal analysis status for {filename}: {status!r}"
                )
            print(
                f"VirusTotal analysis for {filename}: {status}; waiting...",
                flush=True,
            )
            time.sleep(self.poll_interval)
        raise VirusTotalError(
            f"VirusTotal analysis timed out after "
            f"{self.analysis_timeout / 60:.0f} minutes for {filename}"
        )

    def scan(self, path: Path) -> ScanResult:
        sha256 = sha256_file(path)
        analysis_id = self.start_analysis(path, sha256)
        stats = self.wait_for_analysis(analysis_id, path.name)
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
        verdict = "clean" if malicious == 0 and suspicious == 0 else "unsafe"
        return ScanResult(
            file=path.name,
            sha256=sha256,
            size=path.stat().st_size,
            analysis_id=analysis_id,
            malicious=malicious,
            suspicious=suspicious,
            harmless=stats.get("harmless", 0),
            undetected=stats.get("undetected", 0),
            timeout=stats.get("timeout", 0)
            + stats.get("confirmed-timeout", 0),
            failure=stats.get("failure", 0),
            unsupported=stats.get("type-unsupported", 0),
            verdict=verdict,
            permalink=f"https://www.virustotal.com/gui/file/{sha256}/detection",
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
            "Every published APK was freshly analysed. The release is blocked when "
            "VirusTotal reports one or more `malicious` or `suspicious` detections."
        ),
        "",
        "| APK | SHA-256 | Malicious | Suspicious | Undetected | Result |",
        "| --- | --- | ---: | ---: | ---: | --- |",
    ]
    for result in results:
        label = "No detections" if result.verdict == "clean" else "Blocked"
        escaped_file = result.file.replace("|", r"\|")
        lines.append(
            f"| {escaped_file} | "
            f"[`{result.sha256[:12]}…`]({result.permalink}) | "
            f"{result.malicious} | {result.suspicious} | "
            f"{result.undetected} | {label} |"
        )
    return "\n".join(lines) + "\n"
