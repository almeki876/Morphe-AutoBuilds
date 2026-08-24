"""Orchestrate fail-closed VirusTotal scanning with persistent SHA-256 reuse.

The scanner hashes/deduplicates locally, serves conclusive SHA-256 hits from a
persistent cache without any VirusTotal request, starts every new lookup/upload
before polling analyses, and checkpoints results after every completed hash.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
import sys
from dataclasses import asdict, fields, replace
from pathlib import Path
from typing import Callable

from scripts.virustotal import (
    HashLookup,
    ScanResult,
    VirusTotalClient,
    VirusTotalError,
    markdown_report,
    sha256_file,
)

CACHE_VERSION = 1
CACHE_ENGINE_CATEGORIES = frozenset({"malicious", "suspicious"})
SCAN_RESULT_FIELDS = {field.name for field in fields(ScanResult)}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", type=Path, default=Path("base-apks"))
    parser.add_argument(
        "--markdown", type=Path, default=Path("virustotal_base_results.md")
    )
    parser.add_argument(
        "--json", type=Path, default=Path("virustotal_base_results.json")
    )
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


def _client(api_key: str) -> VirusTotalClient:
    initial_interval = float(os.environ.get("VT_REQUEST_INTERVAL_SECONDS", "8"))
    return VirusTotalClient(
        api_key,
        request_interval=initial_interval,
        min_request_interval=float(
            os.environ.get("VT_MIN_REQUEST_INTERVAL_SECONDS", "2")
        ),
        rate_success_window=int(os.environ.get("VT_RATE_SUCCESS_WINDOW", "8")),
        poll_interval=float(os.environ.get("VT_POLL_INTERVAL_SECONDS", "30")),
        initial_poll_delay=float(
            os.environ.get("VT_INITIAL_POLL_DELAY_SECONDS", "20")
        ),
        analysis_timeout=float(
            os.environ.get("VT_ANALYSIS_TIMEOUT_SECONDS", "2700")
        ),
        max_retries=int(os.environ.get("VT_MAX_RETRIES", "6")),
        max_analysis_age_days=float(
            os.environ.get("VT_MAX_ANALYSIS_AGE_DAYS", "90")
        ),
    )


def _atomic_write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.part")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _load_cache(path: Path) -> dict[str, ScanResult]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        print(
            f"::warning::Ignoring unreadable VirusTotal hash cache {path}: {error}",
            flush=True,
        )
        return {}
    if not isinstance(payload, dict) or payload.get("version") != CACHE_VERSION:
        print(
            f"::warning::Ignoring unsupported VirusTotal hash cache format in {path}",
            flush=True,
        )
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


def _cache_result(result: ScanResult) -> ScanResult:
    detected_engines = {
        engine: details
        for engine, details in result.engines.items()
        if details.get("category") in CACHE_ENGINE_CATEGORIES
    }
    return (
        result
        if detected_engines == result.engines
        else replace(result, engines=detected_engines)
    )


def _save_cache(path: Path, cache: dict[str, ScanResult]) -> None:
    _atomic_write_json(
        path,
        {
            "version": CACHE_VERSION,
            "results": {
                sha256: asdict(_cache_result(result))
                for sha256, result in sorted(cache.items())
            },
        },
    )


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
            f"::warning::{apk.name}: engine={engine}, "
            f"category={details.get('category')}, "
            f"detection={details.get('result') or 'unspecified'}, "
            f"version={details.get('engine_version') or 'unknown'}, "
            f"update={details.get('engine_update') or 'unknown'}",
            flush=True,
        )
    if result.verdict != "clean" and not detected:
        print(
            f"::warning::{apk.name}: VirusTotal reported detections, but "
            "engine-level details were not returned by the API.",
            flush=True,
        )


def _scan_all(
    client: VirusTotalClient,
    apk_files: list[Path],
    cache: dict[str, ScanResult],
    cache_path: Path,
    on_progress: Callable[[list[ScanResult], list[str]], None] | None = None,
) -> tuple[list[ScanResult], list[str]]:
    results: list[ScanResult] = []
    failures: list[str] = []
    workers = max(
        1,
        min(len(apk_files), int(os.environ.get("VT_WORKERS", "16"))),
    )

    print(
        f"Hashing {len(apk_files)} APK(s) with {workers} worker(s) before "
        "deduplication.",
        flush=True,
    )
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
    if duplicate_count:
        print(
            f"Deduplicated {duplicate_count} duplicate artifact(s) by SHA-256.",
            flush=True,
        )

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

    print(
        f"Persistent cache: {cache_hits} unique hash hit(s), "
        f"{len(misses)} new hash(es).",
        flush=True,
    )
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

    def accept_result(
        digest: str,
        representative: Path,
        scanned: ScanResult,
    ) -> None:
        cache[digest] = scanned
        _save_cache(cache_path, cache)
        for path in hash_groups[digest]:
            result = (
                scanned
                if path == representative
                else _cached_result_for_path(scanned, path)
            )
            results.append(result)
            _report_result(path, result)
        progress()

    prepared: list[tuple[str, Path, HashLookup]] = []

    def prepare_unique(item: tuple[str, Path]) -> tuple[str, Path, HashLookup]:
        digest, apk = item
        lookup = client.lookup_hash(apk, digest)
        return digest, apk, client.prepare_lookup(apk, lookup)

    print(
        "Starting all required VirusTotal lookups/uploads before analysis polling.",
        flush=True,
    )
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="vt-start") as pool:
        pending = {pool.submit(prepare_unique, item): item for item in misses}
        for future in as_completed(pending):
            digest, representative = pending[future]
            try:
                digest, representative, lookup = future.result()
                if lookup.analysis_id is None:
                    accept_result(
                        digest,
                        representative,
                        client.finish_lookup(representative, lookup),
                    )
                else:
                    prepared.append((digest, representative, lookup))
            except (VirusTotalError, OSError, ValueError) as error:
                fail_hash(digest, error)

    if prepared:
        print(
            f"Polling {len(prepared)} started VirusTotal analysis/analyses in parallel.",
            flush=True,
        )

    def finish_unique(
        item: tuple[str, Path, HashLookup],
    ) -> tuple[str, Path, ScanResult]:
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
) -> None:
    markdown = markdown_report(results).replace(
        "## VirusTotal scan results",
        f"## {title}",
        1,
    )
    json_report = (
        json.dumps(
            {
                "results": [asdict(result) for result in results],
                "failures": failures,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )
    for path, content in (
        (markdown_path, markdown),
        (json_path, json_report),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.part")
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(path)


def main() -> int:
    args = parse_args()
    api_key = os.environ.get("VIRUSTOTAL_API_KEY", "").strip()
    if not api_key:
        print(
            "::error::VIRUSTOTAL_API_KEY is missing. "
            "Refusing to publish unscanned APKs.",
            file=sys.stderr,
        )
        return 2

    supported_suffixes = {".apk", ".apkm", ".apks", ".xapk", ".zip"}
    apk_files = sorted(
        path
        for path in args.directory.rglob("*")
        if path.is_file() and path.suffix.casefold() in supported_suffixes
    )
    if not apk_files:
        print(
            "::warning::No APK files were produced; skipping VirusTotal scanning "
            "because the build failure is reported separately."
        )
        _write_reports(args.markdown, args.json, [], [], args.title)
        return 0

    cache = _load_cache(args.cache)
    if cache:
        print(
            f"Loaded {len(cache)} previously verified VirusTotal hash result(s) "
            f"from {args.cache}.",
            flush=True,
        )

    def save_progress(
        current_results: list[ScanResult],
        current_failures: list[str],
    ) -> None:
        _write_reports(
            args.markdown,
            args.json,
            current_results,
            current_failures,
            args.title,
        )

    results, failures = _scan_all(
        _client(api_key),
        apk_files,
        cache,
        args.cache,
        on_progress=save_progress,
    )
    _save_cache(args.cache, cache)
    _write_reports(args.markdown, args.json, results, failures, args.title)

    unsafe = [result for result in results if result.verdict != "clean"]
    if failures:
        print(
            f"::error::{len(failures)} APK(s) could not be conclusively scanned. "
            "Release blocked.",
            file=sys.stderr,
        )
        return 2
    if unsafe:
        print(
            f"::error::VirusTotal flagged {len(unsafe)} APK(s). Release blocked.",
            file=sys.stderr,
        )
        return 1
    print(f"VirusTotal scan passed for all {len(results)} APK(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
