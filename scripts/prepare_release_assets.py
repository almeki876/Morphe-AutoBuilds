"""Collect built APK assets and append per-app build results to the job summary."""

from __future__ import annotations

import os
import shutil
from pathlib import Path


def collect_apks(source: Path, destination: Path) -> list[Path]:
    destination.mkdir(parents=True, exist_ok=True)
    collected: list[Path] = []
    if not source.is_dir():
        return collected
    for apk in sorted(source.rglob("*.apk")):
        target = destination / apk.name
        shutil.copy2(apk, target)
        collected.append(target)
    return collected


def append_build_results(report_root: Path, summary_path: str | None) -> None:
    if not summary_path:
        return
    reports = sorted(report_root.glob("*.txt")) if report_root.is_dir() else []
    with Path(summary_path).open("a", encoding="utf-8") as summary:
        summary.write("## Build results\n")
        if reports:
            for path in reports:
                summary.write(path.read_text(encoding="utf-8", errors="replace"))
        else:
            summary.write("No per-app build result artifacts were found.\n")


def main() -> int:
    print("Collecting built APKs...")
    collected = collect_apks(Path("all-apks"), Path("release-apks"))
    print("APKs in release-apks:")
    for path in collected:
        print(f"  {path.name}")
    if not collected:
        print("  (none)")
    append_build_results(Path("build-results"), os.getenv("GITHUB_STEP_SUMMARY"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
