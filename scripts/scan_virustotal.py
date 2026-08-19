"""CLI orchestration for fail-closed VirusTotal release scanning."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Callable

from scripts.virustotal import (
    ScanResult,
    VirusTotalClient,
    VirusTotalError,
    markdown_report,
    sha256_file,
)


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
        "--title",
        default="VirusTotal scan results",
        help="Heading used in the Markdown report.",
    )
    return parser.parse_args()


def _client(api_key: str) -> VirusTotalClient:
    return VirusTotalClient(
        api_key,
        request_interval=float(os.environ.get("VT_REQUEST_INTERVAL_SECONDS", "16")),
        poll_interval=float(os.environ.get("VT_POLL_INTERVAL_SECONDS", "30")),
        analysis_timeout=float(os.environ.get("VT_ANALYSIS_TIMEOUT_SECONDS", "2700")),
        max_retries=int(os.environ.get("VT_MAX_RETRIES", "6")),
    )


def _scan_all(
    client: VirusTotalClient,
    apk_files: list[Path],
    on_progress: Callable[[list[ScanResult], list[str]], None] | None = None,
) -> tuple[list[ScanResult], list[str]]:
    results: list[ScanResult] = []
    failures: list[str] = []

    def scan_one(apk: Path) -> ScanResult:
        return client.scan(apk)

    workers = max(
        1,
        min(
            len(apk_files),
            int(os.environ.get("VT_WORKERS", "4")),
        ),
    )
    print(
        f"Scanning {len(apk_files)} APK(s) with {workers} concurrent worker(s).",
        flush=True,
    )
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="vt-scan") as pool:
        pending = {pool.submit(scan_one, apk): apk for apk in apk_files}
        for future in as_completed(pending):
            apk = pending[future]
            try:
                result = future.result()
                results.append(result)
                print(
                    f"{apk.name}: malicious={result.malicious}, "
                    f"suspicious={result.suspicious}, verdict={result.verdict}",
                    flush=True,
                )
                detected = [
                    (engine, details)
                    for engine, details in result.engines.items()
                    if details.get("category") in {"malicious", "suspicious"}
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
                        f"::warning::{apk.name}: VirusTotal reported detections, "
                        "but engine-level details were not returned by the API.",
                        flush=True,
                    )
            except (VirusTotalError, OSError, ValueError) as error:
                message = f"{apk.name}: {error}"
                failures.append(message)
                print(f"::error::{message}", file=sys.stderr, flush=True)
            if on_progress:
                on_progress(results, failures)

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
        print("::error::No APK files were found for VirusTotal scanning.", file=sys.stderr)
        return 2

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
        on_progress=save_progress,
    )
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
