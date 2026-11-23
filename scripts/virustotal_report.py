"""VirusTotal scan result types and their Markdown rendering.

Standard library only, by design. ``scripts/merge_virustotal_reports.py``
runs in the Create Integrated Release job, whose Python setup does not
install third-party dependencies, so the merge step must not pull in
``scripts/virustotal.py`` (which imports ``curl_cffi`` for the scan client).
The client module re-exports these names, so existing importers keep working.
"""

from __future__ import annotations

import time
from dataclasses import dataclass


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
        label = "No detections" if result.verdict == "clean" else "Blocked"
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
