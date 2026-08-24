"""Resolve the shared pre-downloaded base APK and expose it to later steps."""

from __future__ import annotations

import json
import os
from pathlib import Path


SUPPORTED_SUFFIXES = {".apk", ".apkm", ".apks", ".xapk"}


def find_input(root: Path) -> Path | None:
    return next(
        (
            path
            for path in sorted(root.rglob("*"))
            if path.is_file() and path.suffix.lower() in SUPPORTED_SUFFIXES
        ),
        None,
    )


def main() -> int:
    root = Path("base-apk-input")
    apk = find_input(root)
    if apk is None:
        print("::error::No pre-downloaded APK input was found.")
        return 1

    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    version = str(manifest["version"])
    github_env = os.getenv("GITHUB_ENV")
    if not github_env:
        raise RuntimeError("GITHUB_ENV is required")
    with Path(github_env).open("a", encoding="utf-8") as handle:
        handle.write(f"PRE_DOWNLOADED_APK={apk}\n")
        handle.write(f"PRE_DOWNLOADED_VERSION={version}\n")
    print(f"Resolved pre-downloaded APK: {apk} (version {version})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
