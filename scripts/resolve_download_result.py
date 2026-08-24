"""Combine APK download attempt logs and resolve the matrix step outcome."""

from __future__ import annotations

import os
import sys
from pathlib import Path


ATTEMPTS = (
    ("Primary download", "download-primary.log"),
    ("Japan fallback download", "download-japan.log"),
    ("Final provider rescue", "download-provider-rescue.log"),
)


def combine_logs(output: Path = Path("download.log")) -> None:
    with output.open("w", encoding="utf-8") as destination:
        for title, filename in ATTEMPTS:
            path = Path(filename)
            if not path.is_file():
                continue
            destination.write(f"===== {title} =====\n")
            destination.write(path.read_text(encoding="utf-8", errors="replace"))
            if destination.tell() and not path.read_text(encoding="utf-8", errors="replace").endswith("\n"):
                destination.write("\n")


def outcome_message(primary: str, japan: str, rescue: str) -> tuple[bool, str]:
    if primary == "success":
        return True, "Primary download succeeded; fallbacks were not used."
    if japan == "success":
        return True, "Primary download failed; Japanese Tailscale fallback succeeded."
    if rescue == "success":
        return True, "Google Play/Japan path did not succeed; final provider rescue succeeded."
    return False, "Primary, Japanese Tailscale, and final provider rescue did not succeed."


def main() -> int:
    combine_logs()
    success, message = outcome_message(
        os.getenv("PRIMARY_OUTCOME", ""),
        os.getenv("JAPAN_OUTCOME", ""),
        os.getenv("PROVIDER_RESCUE_OUTCOME", ""),
    )
    print(message, file=sys.stdout if success else sys.stderr)
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
