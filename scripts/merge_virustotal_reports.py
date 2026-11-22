"""Merge individual per-app VirusTotal scan reports into a single consolidated report."""

from __future__ import annotations

import argparse
from dataclasses import asdict, fields
import json
from pathlib import Path
import sys
from typing import Any

from scripts.virustotal import ScanResult, markdown_report

CACHE_VERSION = 1
SCAN_RESULT_FIELDS = {field.name for field in fields(ScanResult)}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Merge per-app VirusTotal JSON reports.")
    parser.add_argument(
        "--directory",
        type=Path,
        default=Path("vt-chunks"),
        help="Directory containing per-app virustotal report files",
    )
    parser.add_argument(
        "--output-json",
        type=Path,
        default=Path("virustotal_base_results.json"),
        help="Path for consolidated JSON report",
    )
    parser.add_argument(
        "--output-markdown",
        type=Path,
        default=Path("virustotal_base_results.md"),
        help="Path for consolidated Markdown report",
    )
    parser.add_argument(
        "--title",
        default="VirusTotal base APK scan results (before patching)",
        help="Heading title used in the Markdown report",
    )
    return parser.parse_args()


def load_chunk_results(directory: Path) -> tuple[list[dict[str, Any]], list[str], dict[str, Any]]:
    combined_results: dict[str, dict[str, Any]] = {}
    combined_failures: set[str] = set()
    combined_cache: dict[str, Any] = {}

    if not directory.is_dir():
        return [], [], {}

    json_files = sorted(
        p for p in directory.rglob("*.json")
        if p.is_file() and not p.name.startswith(".")
    )

    for path in json_files:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            print(f"::warning::Skipping invalid VirusTotal chunk {path}: {error}", flush=True)
            continue

        if not isinstance(data, dict):
            continue

        for item in data.get("results") or []:
            if isinstance(item, dict) and item.get("sha256"):
                sha = str(item["sha256"])
                combined_results[sha] = item

        for failure in data.get("failures") or []:
            if isinstance(failure, str) and failure.strip():
                combined_failures.add(failure.strip())

        cache_data = data.get("cache")
        if isinstance(cache_data, dict):
            cache_results = cache_data.get("results")
            if isinstance(cache_results, dict):
                combined_cache.update(cache_results)

    # Sort results by file name, then sha256
    sorted_results = sorted(
        combined_results.values(),
        key=lambda r: (str(r.get("file") or ""), str(r.get("sha256") or "")),
    )
    return sorted_results, sorted(combined_failures), combined_cache


def dict_to_scan_result(raw: dict[str, Any]) -> ScanResult | None:
    try:
        kwargs: dict[str, Any] = {
            "file": str(raw.get("file", "unknown.apk")),
            "sha256": str(raw.get("sha256", "")),
            "size": int(raw.get("size", 0)),
            "analysis_id": str(raw.get("analysis_id", "")),
            "malicious": int(raw.get("malicious", 0)),
            "suspicious": int(raw.get("suspicious", 0)),
            "harmless": int(raw.get("harmless", 0)),
            "undetected": int(raw.get("undetected", 0)),
            "timeout": int(raw.get("timeout", 0)),
            "failure": int(raw.get("failure", 0)),
            "unsupported": int(raw.get("unsupported", 0)),
            "verdict": str(raw.get("verdict", "clean")),
            "method": str(raw.get("method", "sha256")),
            "engines": dict(raw.get("engines", {})),
            "permalink": str(raw.get("permalink", "")),
            "last_analysis_date": raw.get("last_analysis_date"),
            "reanalyzed": bool(raw.get("reanalyzed", False)),
        }
        for k in SCAN_RESULT_FIELDS:
            if k in raw:
                kwargs[k] = raw[k]
        return ScanResult(**kwargs)
    except (TypeError, ValueError, KeyError):
        return None


def merge_reports(
    directory: Path,
    output_json: Path,
    output_markdown: Path,
    title: str,
) -> int:
    results, failures, cache = load_chunk_results(directory)
    if not results and not failures:
        print(f"No VirusTotal scan reports found in {directory}; skipping consolidated report.", flush=True)
        return 0

    scan_results: list[ScanResult] = []
    for r in results:
        obj = dict_to_scan_result(r)
        if obj is not None:
            scan_results.append(obj)

    markdown = markdown_report(scan_results).replace("## VirusTotal scan results", f"## {title}", 1)

    payload: dict[str, Any] = {
        "results": results,
        "failures": failures,
        "cache": {
            "version": CACHE_VERSION,
            "results": cache,
        },
    }

    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    output_markdown.parent.mkdir(parents=True, exist_ok=True)
    output_markdown.write_text(markdown, encoding="utf-8")

    print(
        f"Successfully merged {len(results)} VirusTotal result(s) into {output_json} and {output_markdown}",
        flush=True,
    )
    return 0


def main() -> int:
    args = parse_args()
    return merge_reports(
        args.directory,
        args.output_json,
        args.output_markdown,
        args.title,
    )


if __name__ == "__main__":
    raise SystemExit(main())
