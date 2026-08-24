"""Create the integrated APK release and publish its VirusTotal SHA cache."""

from __future__ import annotations

import os
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from scripts.export_virustotal_cache import export_cache


RELEASE_DIR = Path("release-apks")
VT_REPORT = Path("virustotal_base_results.json")
VT_CACHE = Path("virustotal-cache-v1.json")


def run(command: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=check, text=True)


def upload_apk(tag: str, path: Path) -> None:
    run(["gh", "release", "upload", tag, str(path)])


def create_release(tag: str, repository: str) -> None:
    title = f"Morphe AutoBuilds {tag}"
    print(f"Creating release: {tag}")

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

    apks = sorted(RELEASE_DIR.glob("*.apk"))
    if not apks:
        raise RuntimeError("No APK assets found in release-apks")
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
