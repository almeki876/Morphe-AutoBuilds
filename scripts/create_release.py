"""Create the integrated APK release and publish its VirusTotal SHA cache."""

from __future__ import annotations

import os
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from scripts import virustotal_gate
from scripts.export_virustotal_cache import export_cache


RELEASE_DIR = Path("release-apks")
VT_REPORT = Path("virustotal_base_results.json")
VT_CACHE = Path("virustotal-cache-v1.json")
ARTIFACT_ROOT = Path("all-apks")
REPORT_ROOT = Path("build-results")
EXCLUSIONS_PATH = Path("virustotal_exclusions.json")


def run(command: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=check, text=True)


def upload_apk(tag: str, path: Path) -> None:
    run(["gh", "release", "upload", tag, str(path)])


def create_release(tag: str, repository: str) -> None:
    title = f"Morphe AutoBuilds {tag}"
    print(f"Creating release: {tag}")

    # VirusTotal gate: withhold every app whose base APK did not come back
    # clean (or could not be conclusively scanned) before anything is
    # published.  Only the flagged apps are removed; the rest of the
    # release proceeds.  The decision is recorded in
    # virustotal_exclusions.json and in the release notes.
    exclusions = virustotal_gate.enforce_release_gate(
        vt_report=VT_REPORT,
        artifact_root=ARTIFACT_ROOT,
        report_root=REPORT_ROOT,
        release_dir=RELEASE_DIR,
        exclusions_path=EXCLUSIONS_PATH,
        release_tag=tag,
    )
    if exclusions:
        print(
            f"VirusTotal gate withheld {len(exclusions)} app(s) from this release: "
            + ", ".join(
                f"{item.app_name} ({item.source})" for item in exclusions
            )
        )

    apks = sorted(RELEASE_DIR.glob("*.apk"))
    if not apks:
        raise RuntimeError("No APK assets found in release-apks")

    run(["gh", "release", "delete", tag, "--yes"], check=False)
    run(["git", "push", "--delete", "origin", f"refs/tags/{tag}"], check=False)
    run(
        [
            "gh",
            "release",
            "create",
            tag,
            "--title",
            title,
            "--notes-file",
            "release_notes.md",
        ]
    )

    with ThreadPoolExecutor(max_workers=min(8, len(apks))) as executor:
        list(executor.map(lambda path: upload_apk(tag, path), apks))

    count = export_cache(VT_REPORT, VT_CACHE)
    if not VT_CACHE.is_file() or VT_CACHE.stat().st_size == 0:
        raise RuntimeError("VirusTotal portable cache export was empty")
    run(
        [
            "gh",
            "release",
            "upload",
            tag,
            str(VT_CACHE),
            "--repo",
            repository,
            "--clobber",
        ]
    )
    print(f"VirusTotal SHA cache attached to {tag} ({count} cached result(s))")
    print("Release created and APK assets uploaded successfully!")
    print(f"Release URL: https://github.com/{repository}/releases/tag/{tag}")


def main() -> int:
    tag = os.getenv("RELEASE_TAG", "").strip()
    repository = os.getenv("GITHUB_REPOSITORY", "").strip()
    if not tag:
        raise RuntimeError("RELEASE_TAG is required")
    if not repository:
        raise RuntimeError("GITHUB_REPOSITORY is required")
    create_release(tag, repository)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
