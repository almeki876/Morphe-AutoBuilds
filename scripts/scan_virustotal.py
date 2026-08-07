"""CLI orchestration for fail-closed VirusTotal release scanning."""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict
from pathlib import Path

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
    client: VirusTotalClient, apk_files: list[Path]
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
            results.append(result)
            print(
                f"{apk.name}: malicious={result.malicious}, "
                f"suspicious={result.suspicious}, verdict={result.verdict}",
                flush=True,
            )
        except VirusTotalError as error:
            message = f"{apk.name}: {error}"
            failures.append(message)
            print(f"::error::{message}", file=sys.stderr, flush=True)
            # API-wide failures normally affect every remaining file.
            break
        except (OSError, ValueError) as error:
            message = f"{apk.name}: {error}"
            failures.append(message)
            print(f"::error::{message}", file=sys.stderr, flush=True)
    return results, failures


def _write_reports(
    markdown_path: Path,
    json_path: Path,
    results: list[ScanResult],
    failures: list[str],
) -> None:
    markdown_path.write_text(markdown_report(results), encoding="utf-8")
    json_path.write_text(
        json.dumps(
            {
                "results": [asdict(result) for result in results],
                "failures": failures,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


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

    apk_files = sorted(args.directory.rglob("*.apk"))
    if not apk_files:
        print("::error::No APK files were found for VirusTotal scanning.", file=sys.stderr)
        return 2

    results, failures = _scan_all(_client(api_key), apk_files)
    _write_reports(args.markdown, args.json, results, failures)

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
