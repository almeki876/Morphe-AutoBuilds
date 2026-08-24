"""Orchestrate fail-closed VirusTotal scanning with durable SHA-256 reuse.

The scanner hashes/deduplicates locally, serves conclusive SHA-256 hits from a
persistent cache without VirusTotal requests, can recover that cache from the
latest GitHub Release, starts every new lookup/upload before polling analyses,
and emits performance telemetry for tuning the pipeline.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, fields, replace
import json
import os
from pathlib import Path
import sys
import threading
import time
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from scripts.virustotal import (
    HashLookup,
    ScanResult,
    VirusTotalClient,
    VirusTotalError,
    markdown_report,
    sha256_file,
)

CACHE_VERSION = 1
CACHE_ASSET_NAME = "virustotal-cache-v1.json"
CACHE_ENGINE_CATEGORIES = frozenset({"malicious", "suspicious"})
SCAN_RESULT_FIELDS = {field.name for field in fields(ScanResult)}


@dataclass
class ScanTelemetry:
    total_files: int = 0
    unique_hashes: int = 0
    duplicate_files: int = 0
    local_cache_entries: int = 0
    release_cache_entries_added: int = 0
    cache_hits: int = 0
    new_hashes: int = 0
    existing_vt_hash_hits: int = 0
    analyses_started: int = 0
    elapsed_seconds: float = 0.0


class InstrumentedVirusTotalClient(VirusTotalClient):
    """VirusTotalClient with thread-safe operational counters."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._metrics_lock = threading.Lock()
        self._metrics = {
            "logical_requests": 0,
            "hash_lookups": 0,
            "uploads": 0,
            "reanalysis_requests": 0,
            "analysis_polls": 0,
            "rate_limit_backoffs": 0,
            "rate_accelerations": 0,
        }
        self._interval_samples = 0
        self._interval_sum = 0.0

    def request(self, method: str, url: str, **kwargs):
        interval = self.current_request_interval
        with self._metrics_lock:
            self._metrics["logical_requests"] += 1
            self._interval_samples += 1
            self._interval_sum += interval
            upper_method = method.upper()
            if upper_method == "GET" and "/analyses/" in url:
                self._metrics["analysis_polls"] += 1
            elif upper_method == "POST" and url.rstrip("/").endswith("/analyse"):
                self._metrics["reanalysis_requests"] += 1
            elif upper_method == "GET" and "/files/" in url and not url.endswith("upload_url"):
                self._metrics["hash_lookups"] += 1
            elif upper_method == "POST":
                self._metrics["uploads"] += 1
        return super().request(method, url, **kwargs)

    def _observe_rate_limit(self, retry_after: float) -> None:
        with self._metrics_lock:
            self._metrics["rate_limit_backoffs"] += 1
        super()._observe_rate_limit(retry_after)

    def _observe_success(self) -> None:
        before = self.current_request_interval
        super()._observe_success()
        after = self.current_request_interval
        if after < before:
            with self._metrics_lock:
                self._metrics["rate_accelerations"] += 1

    def telemetry_snapshot(self) -> dict[str, int | float]:
        with self._metrics_lock:
            payload: dict[str, int | float] = dict(self._metrics)
            payload["average_request_interval_seconds"] = (
                self._interval_sum / self._interval_samples
                if self._interval_samples
                else 0.0
            )
        payload["final_request_interval_seconds"] = self.current_request_interval
        return payload


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", type=Path, default=Path("base-apks"))
    parser.add_argument("--markdown", type=Path, default=Path("virustotal_base_results.md"))
    parser.add_argument("--json", type=Path, default=Path("virustotal_base_results.json"))
    parser.add_argument(
        "--cache",
        type=Path,
        default=Path(".cache/virustotal/hash-results.json"),
        help="Persistent SHA-256 -> conclusive VirusTotal result cache.",
    )
    parser.add_argument(
        "--title",
        default="VirusTotal scan results",
        help="Heading used in the Markdown report.",
    )
    return parser.parse_args()


def _client(api_key: str) -> InstrumentedVirusTotalClient:
    initial_interval = float(os.environ.get("VT_REQUEST_INTERVAL_SECONDS", "8"))
    return InstrumentedVirusTotalClient(
        api_key,
        request_interval=initial_interval,
        min_request_interval=float(os.environ.get("VT_MIN_REQUEST_INTERVAL_SECONDS", "2")),
        rate_success_window=int(os.environ.get("VT_RATE_SUCCESS_WINDOW", "8")),
        poll_interval=float(os.environ.get("VT_POLL_INTERVAL_SECONDS", "30")),
        initial_poll_delay=float(os.environ.get("VT_INITIAL_POLL_DELAY_SECONDS", "20")),
        analysis_timeout=float(os.environ.get("VT_ANALYSIS_TIMEOUT_SECONDS", "2700")),
        max_retries=int(os.environ.get("VT_MAX_RETRIES", "6")),
        max_analysis_age_days=float(os.environ.get("VT_MAX_ANALYSIS_AGE_DAYS", "90")),
    )


def _atomic_write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.part")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _parse_cache_payload(payload: object) -> dict[str, ScanResult]:
    if not isinstance(payload, dict) or payload.get("version") != CACHE_VERSION:
        return {}
    raw_results = payload.get("results")
    if not isinstance(raw_results, dict):
        return {}

    cache: dict[str, ScanResult] = {}
    for sha256, raw in raw_results.items():
        if not isinstance(sha256, str) or not isinstance(raw, dict):
            continue
        try:
            normalized = {key: raw[key] for key in SCAN_RESULT_FIELDS}
            result = ScanResult(**normalized)
        except (KeyError, TypeError, ValueError):
            continue
        if result.sha256 == sha256:
            cache[sha256] = result
    return cache


def _load_cache(path: Path) -> dict[str, ScanResult]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        print(f"::warning::Ignoring unreadable VirusTotal hash cache {path}: {error}", flush=True)
        return {}
    cache = _parse_cache_payload(payload)
    if not cache and payload:
        print(f"::warning::Ignoring unsupported VirusTotal hash cache format in {path}", flush=True)
    return cache


def _cache_result(result: ScanResult) -> ScanResult:
    detected_engines = {
        engine: details
        for engine, details in result.engines.items()
        if details.get("category") in CACHE_ENGINE_CATEGORIES
    }
    return result if detected_engines == result.engines else replace(result, engines=detected_engines)


def _cache_payload(cache: dict[str, ScanResult]) -> dict[str, object]:
    return {
        "version": CACHE_VERSION,
        "results": {
            sha256: asdict(_cache_result(result))
            for sha256, result in sorted(cache.items())
        },
    }


def _save_cache(path: Path, cache: dict[str, ScanResult]) -> None:
    _atomic_write_json(path, _cache_payload(cache))


def _release_cache_url(repository: str) -> str:
    return f"https://github.com/{repository}/releases/latest/download/{CACHE_ASSET_NAME}"


def _restore_release_cache(cache: dict[str, ScanResult]) -> int:
    """Merge a portable cache from the latest Release, preferring local entries."""
    if os.environ.get("VT_RELEASE_CACHE", "true").casefold() == "false":
        return 0
    repository = os.environ.get("GITHUB_REPOSITORY", "").strip()
    if not repository or "/" not in repository:
        return 0

    request = Request(
        _release_cache_url(repository),
        headers={"User-Agent": "Morphe-AutoBuilds-VirusTotal-cache/1"},
    )
    try:
        with urlopen(request, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        if error.code != 404:
            print(f"::warning::VirusTotal Release cache HTTP {error.code}; continuing without it", flush=True)
        return 0
    except (URLError, TimeoutError, OSError, ValueError) as error:
        print(f"::warning::Could not restore VirusTotal Release cache: {error}", flush=True)
        return 0

    release_cache = _parse_cache_payload(payload)
    added = 0
    for sha256, result in release_cache.items():
        if sha256 not in cache:
            cache[sha256] = result
            added += 1
    if added:
        print(
            f"Recovered {added} additional VirusTotal SHA result(s) from the latest Release.",
            flush=True,
        )
    return added


def _cached_result_for_path(result: ScanResult, path: Path) -> ScanResult:
    return replace(
        result,
        file=path.name,
        size=path.stat().st_size,
        method="persistent hash cache",
        reanalyzed=False,
    )


def _report_result(apk: Path, result: ScanResult) -> None:
    print(
        f"{apk.name}: malicious={result.malicious}, suspicious={result.suspicious}, "
        f"verdict={result.verdict}, method={result.method}",
        flush=True,
    )
    detected = [
        (engine, details)
        for engine, details in result.engines.items()
        if details.get("category") in CACHE_ENGINE_CATEGORIES
    ]
    for engine, details in sorted(detected):
        print(
            f"::warning::{apk.name}: engine={engine}, category={details.get('category')}, "
            f"detection={details.get('result') or 'unspecified'}, "
            f"version={details.get('engine_version') or 'unknown'}, "
            f"update={details.get('engine_update') or 'unknown'}",
            flush=True,
        )
    if result.verdict != "clean" and not detected:
        print(
            f"::warning::{apk.name}: VirusTotal reported detections, but engine-level details were not returned by the API.",
            flush=True,
        )


def _scan_all(
    client: VirusTotalClient,
    apk_files: list[Path],
    cache: dict[str, ScanResult],
    cache_path: Path,
    on_progress: Callable[[list[ScanResult], list[str]], None] | None = None,
    telemetry: ScanTelemetry | None = None,
) -> tuple[list[ScanResult], list[str]]:
    results: list[ScanResult] = []
    failures: list[str] = []
    workers = max(1, min(len(apk_files), int(os.environ.get("VT_WORKERS", "16"))))
    if telemetry:
        telemetry.total_files = len(apk_files)

    print(f"Hashing {len(apk_files)} APK(s) with {workers} worker(s) before deduplication.", flush=True)
    hash_groups: dict[str, list[Path]] = {}
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="vt-hash") as pool:
        pending = {pool.submit(sha256_file, apk): apk for apk in apk_files}
        for future in as_completed(pending):
            apk = pending[future]
            try:
                digest = future.result()
            except OSError as error:
                message = f"{apk.name}: {error}"
                failures.append(message)
                print(f"::error::{message}", file=sys.stderr, flush=True)
                continue
            hash_groups.setdefault(digest, []).append(apk)

    duplicate_count = len(apk_files) - len(hash_groups)
    if telemetry:
        telemetry.unique_hashes = len(hash_groups)
        telemetry.duplicate_files = duplicate_count
    if duplicate_count:
        print(f"Deduplicated {duplicate_count} duplicate artifact(s) by SHA-256.", flush=True)

    misses: list[tuple[str, Path]] = []
    cache_hits = 0
    for digest, paths in sorted(hash_groups.items()):
        cached = cache.get(digest)
        if cached is None:
            misses.append((digest, paths[0]))
            continue
        cache_hits += 1
        for path in paths:
            result = _cached_result_for_path(cached, path)
            results.append(result)
            _report_result(path, result)

    if telemetry:
        telemetry.cache_hits = cache_hits
        telemetry.new_hashes = len(misses)
    print(f"Persistent cache: {cache_hits} unique hash hit(s), {len(misses)} new hash(es).", flush=True)
    if not misses:
        results.sort(key=lambda result: result.file)
        failures.sort()
        return results, failures

    def progress() -> None:
        if on_progress:
            on_progress(results, failures)

    def fail_hash(digest: str, error: Exception) -> None:
        for path in hash_groups[digest]:
            message = f"{path.name}: {error}"
            failures.append(message)
            print(f"::error::{message}", file=sys.stderr, flush=True)
        progress()

    def accept_result(digest: str, representative: Path, scanned: ScanResult) -> None:
        cache[digest] = scanned
        _save_cache(cache_path, cache)
        for path in hash_groups[digest]:
            result = scanned if path == representative else _cached_result_for_path(scanned, path)
            results.append(result)
            _report_result(path, result)
        progress()

    prepared: list[tuple[str, Path, HashLookup]] = []

    def prepare_unique(item: tuple[str, Path]) -> tuple[str, Path, HashLookup]:
        digest, apk = item
        lookup = client.lookup_hash(apk, digest)
        return digest, apk, client.prepare_lookup(apk, lookup)

    print("Starting all required VirusTotal lookups/uploads before analysis polling.", flush=True)
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="vt-start") as pool:
        pending = {pool.submit(prepare_unique, item): item for item in misses}
        for future in as_completed(pending):
            digest, representative = pending[future]
            try:
                digest, representative, lookup = future.result()
                if lookup.analysis_id is None:
                    if telemetry:
                        telemetry.existing_vt_hash_hits += 1
                    accept_result(digest, representative, client.finish_lookup(representative, lookup))
                else:
                    if telemetry:
                        telemetry.analyses_started += 1
                    prepared.append((digest, representative, lookup))
            except (VirusTotalError, OSError, ValueError) as error:
                fail_hash(digest, error)

    if prepared:
        print(f"Polling {len(prepared)} started VirusTotal analysis/analyses in parallel.", flush=True)

    def finish_unique(item: tuple[str, Path, HashLookup]) -> tuple[str, Path, ScanResult]:
        digest, apk, lookup = item
        return digest, apk, client.finish_lookup(apk, lookup)

    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="vt-poll") as pool:
        pending = {pool.submit(finish_unique, item): item for item in prepared}
        for future in as_completed(pending):
            digest, representative, _lookup = pending[future]
            try:
                digest, representative, scanned = future.result()
                accept_result(digest, representative, scanned)
            except (VirusTotalError, OSError, ValueError) as error:
                fail_hash(digest, error)

    results.sort(key=lambda result: result.file)
    failures.sort()
    return results, failures


def _write_reports(
    markdown_path: Path,
    json_path: Path,
    results: list[ScanResult],
    failures: list[str],
    title: str,
    *,
    cache: dict[str, ScanResult] | None = None,
    telemetry: ScanTelemetry | None = None,
    client_metrics: dict[str, int | float] | None = None,
) -> None:
    markdown = markdown_report(results).replace("## VirusTotal scan results", f"## {title}", 1)
    payload: dict[str, object] = {
        "results": [asdict(result) for result in results],
        "failures": failures,
    }
    if telemetry is not None:
        payload["telemetry"] = asdict(telemetry)
    if client_metrics is not None:
        payload["api_metrics"] = client_metrics
    if cache is not None:
        payload["cache"] = _cache_payload(cache)
    json_report = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    for path, content in ((markdown_path, markdown), (json_path, json_report)):
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.part")
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(path)


def _write_github_summary(
    telemetry: ScanTelemetry,
    client_metrics: dict[str, int | float],
) -> None:
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY", "").strip()
    if not summary_path:
        return
    rows = [
        ("APK files", telemetry.total_files),
        ("Unique SHA-256", telemetry.unique_hashes),
        ("Duplicate files avoided", telemetry.duplicate_files),
        ("Local cache entries", telemetry.local_cache_entries),
        ("Release cache entries recovered", telemetry.release_cache_entries_added),
        ("Unique cache hits", telemetry.cache_hits),
        ("New hashes requiring VT", telemetry.new_hashes),
        ("Existing VT hash hits", telemetry.existing_vt_hash_hits),
        ("Analyses started", telemetry.analyses_started),
        ("Logical VT API calls", client_metrics.get("logical_requests", 0)),
        ("Hash lookups", client_metrics.get("hash_lookups", 0)),
        ("Uploads", client_metrics.get("uploads", 0)),
        ("Reanalysis requests", client_metrics.get("reanalysis_requests", 0)),
        ("Analysis polls", client_metrics.get("analysis_polls", 0)),
        ("Rate-limit backoffs", client_metrics.get("rate_limit_backoffs", 0)),
        ("Rate accelerations", client_metrics.get("rate_accelerations", 0)),
        ("Average request interval", f"{float(client_metrics.get('average_request_interval_seconds', 0)):.2f}s"),
        ("Final request interval", f"{float(client_metrics.get('final_request_interval_seconds', 0)):.2f}s"),
        ("VirusTotal stage elapsed", f"{telemetry.elapsed_seconds:.1f}s"),
    ]
    with Path(summary_path).open("a", encoding="utf-8") as stream:
        stream.write("\n## VirusTotal performance\n\n| Metric | Value |\n| --- | ---: |\n")
        for key, value in rows:
            stream.write(f"| {key} | {value} |\n")


def main() -> int:
    args = parse_args()
    api_key = os.environ.get("VIRUSTOTAL_API_KEY", "").strip()
    if not api_key:
        print("::error::VIRUSTOTAL_API_KEY is missing. Refusing to publish unscanned APKs.", file=sys.stderr)
        return 2

    started = time.monotonic()
    telemetry = ScanTelemetry()
    supported_suffixes = {".apk", ".apkm", ".apks", ".xapk", ".zip"}
    apk_files = sorted(
        path
        for path in args.directory.rglob("*")
        if path.is_file() and path.suffix.casefold() in supported_suffixes
    )

    cache = _load_cache(args.cache)
    telemetry.local_cache_entries = len(cache)
    telemetry.release_cache_entries_added = _restore_release_cache(cache)
    if cache:
        _save_cache(args.cache, cache)
        print(f"Loaded {len(cache)} reusable VirusTotal hash result(s).", flush=True)

    if not apk_files:
        telemetry.elapsed_seconds = time.monotonic() - started
        _write_reports(args.markdown, args.json, [], [], args.title, cache=cache, telemetry=telemetry)
        print("::warning::No APK files were produced; skipping VirusTotal scanning because the build failure is reported separately.")
        return 0

    client = _client(api_key)

    def save_progress(current_results: list[ScanResult], current_failures: list[str]) -> None:
        _write_reports(
            args.markdown,
            args.json,
            current_results,
            current_failures,
            args.title,
            cache=cache,
            telemetry=telemetry,
            client_metrics=client.telemetry_snapshot(),
        )

    results, failures = _scan_all(
        client,
        apk_files,
        cache,
        args.cache,
        on_progress=save_progress,
        telemetry=telemetry,
    )
    telemetry.elapsed_seconds = time.monotonic() - started
    _save_cache(args.cache, cache)
    client_metrics = client.telemetry_snapshot()
    _write_reports(
        args.markdown,
        args.json,
        results,
        failures,
        args.title,
        cache=cache,
        telemetry=telemetry,
        client_metrics=client_metrics,
    )
    _write_github_summary(telemetry, client_metrics)

    unsafe = [result for result in results if result.verdict != "clean"]
    if failures:
        print(f"::error::{len(failures)} APK(s) could not be conclusively scanned. Release blocked.", file=sys.stderr)
        return 2
    if unsafe:
        print(f"::error::VirusTotal flagged {len(unsafe)} APK(s). Release blocked.", file=sys.stderr)
        return 1
    print(f"VirusTotal scan passed for all {len(results)} APK(s) in {telemetry.elapsed_seconds:.1f}s.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
