"""Print the final integrated release summary."""

from __future__ import annotations

import os
from pathlib import Path


def main() -> int:
    tag = os.environ["RELEASE_TAG"]
    repository = os.environ["GITHUB_REPOSITORY"]
    apks = sorted(Path("release-apks").glob("*.apk"))
    print("Release Summary:")
    print(f"  Tag: {tag}")
    print("  APKs:")
    for apk in apks:
        print(f"    - {apk}")
    print()
    print(f"Download: https://github.com/{repository}/releases/tag/{tag}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
