"""Validate release APK assets and generate release metadata."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def write_output(name: str, value: str) -> None:
    output = os.getenv("GITHUB_OUTPUT")
    if not output:
        raise RuntimeError("GITHUB_OUTPUT is required")
    with Path(output).open("a", encoding="utf-8") as handle:
        handle.write(f"{name}={value}\n")


def main() -> int:
    total = sum(1 for _ in Path("release-apks").glob("*.apk"))
    print(f"Total APKs built: {total}")
    if total == 0:
        print("::error::No APK artifacts were produced. Refusing to report a successful release job.")
        write_output("skip_release", "true")
        return 1

    write_output("skip_release", "false")
    subprocess.run([sys.executable, "-m", "scripts.release_metadata"], check=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
