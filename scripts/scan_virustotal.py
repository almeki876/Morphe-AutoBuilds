"""CLI orchestration for fail-closed VirusTotal release scanning."""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict, replace
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
    parser.add_argument("--directory", type=Path, default=Path("release-apks"))
    parser.add_argument(
        "--markdown", type=Path, default=Path("virustotal_results.md")
    )
    parser.add_argument("--json", type=Path, default=Path("virustotal_results.json"))
    parser.add_argument(
        "--title",
        default="VirusTotal scan results",
        help="Heading used in the Markdown report.",
    )
    parser.add_argument(
        "--allow-known-repack-detections",
        action="store_true",
        help=(
            "Accept only the exact generic repack/PUP signatures documented "
            "by this repository. Use only after the corresponding unmodified "
            "base APK scan has passed."
        ),
    )
    parser.add_argument(
        "--baseline-json",
        type=Path,
        help=(
            "JSON report from the corresponding unmodified base APK scan; "
            "required with --allow-known-repack-detections"
        ),
    )
    return parser.parse_args()


# These two signatures appeared consistently only after patching/re-signing
# otherwise clean base APKs. Keep the match deliberately exact: a new engine,
# category, or detection name remains release-blocking until reviewed.
KNOWN_REPACK_DETECTIONS = {
    ("AhnLab-V3", "malicious", "PUP/Android.Malct.1228668"),
    ("BitDefenderFalx", "malicious", "Android.Riskware.Repack.rXJBC"),
}


def _apply_known_repack_policy(result: ScanResult) -> ScanResult:
    if result.verdict == "clean":
        return result
    detected = {
        (
            engine,
            str(details.get("category") or ""),
            str(details.get("result") or ""),
        )
        for engine, details in result.engines.items()
        if details.get("category") in {"malicious", "suspicious"}
    }
    detection_count = result.malicious + result.suspicious
    if (
        detected
        and len(detected) == detection_count
        and detected <= KNOWN_REPACK_DETECTIONS
    ):
        return replace(result, verdict="accepted-repack")
    return result


def _verify_clean_baseline(path: Path | None, expected_count: int) -> None:
    if path is None:
        raise ValueError(
            "--baseline-json is required with --allow-known-repack-detections"
        )
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"could not read base APK scan report: {error}") from error
    results = report.get("results")
    failures = report.get("failures")
    if not isinstance(results, list) or not isinstance(failures, list):
        raise ValueError("base APK scan report has an invalid structure")
    if failures:
        raise ValueError("base APK scan report contains failures")
    if len(results) != expected_count:
        raise ValueError(
            "base/final APK scan count mismatch: "
            f"{len(results)} base vs {expected_count} final"
        )
    if any(result.get("verdict") != "clean" for result in results):
        raise ValueError("base APK scan report contains a non-clean verdict")


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
    allow_known_repack_detections: bool = False,
) -> tuple[list[ScanResult], list[str]]:
    results: list[ScanResult] = []
    failures: list[str] = []
    by_hash: dict[str, ScanResult] = {}

    for apk in apk_files:
        try:
            sha256 = sha256_file(apk)
            if sha256 in by_hash:
                result = ScanResult(
                    **{
                        **asdict(by_hash[sha256]),
                        "file": apk.name,
                        "size": apk.stat().st_size,
                    }
                )
            else:
                result = client.scan(apk)
                by_hash[result.sha256] = result
            if allow_known_repack_detections:
                result = _apply_known_repack_policy(result)
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
            if result.verdict == "accepted-repack":
                print(
                    f"::warning::{apk.name}: only reviewed generic repack/PUP "
                    "detections remain; accepted because the unmodified base "
                    "APK scan passed",
                    flush=True,
                )
            if on_progress:
                on_progress(results, failures)
        except VirusTotalError as error:
            message = f"{apk.name}: {error}"
            failures.append(message)
            print(f"::error::{message}", file=sys.stderr, flush=True)
            if on_progress:
                on_progress(results, failures)
            # API-wide failures normally affect every remaining file.
            break
        except (OSError, ValueError) as error:
            message = f"{apk.name}: {error}"
            failures.append(message)
            print(f"::error::{message}", file=sys.stderr, flush=True)
            if on_progress:
                on_progress(results, failures)
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

    if args.allow_known_repack_detections:
        try:
            _verify_clean_baseline(args.baseline_json, len(apk_files))
        except ValueError as error:
            print(f"::error::{error}. Release blocked.", file=sys.stderr)
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
        allow_known_repack_detections=args.allow_known_repack_detections,
    )
    _write_reports(args.markdown, args.json, results, failures, args.title)

    unsafe = [
        result
        for result in results
        if result.verdict not in {"clean", "accepted-repack"}
    ]
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
