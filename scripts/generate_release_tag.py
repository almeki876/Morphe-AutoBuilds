"""Generate the JST timestamp release tag and expose it as a step output."""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


def main() -> int:
    tag = datetime.now(ZoneInfo("Asia/Tokyo")).strftime("%Y-%m-%d_%H-%M-JST")
    output = os.getenv("GITHUB_OUTPUT")
    if not output:
        raise RuntimeError("GITHUB_OUTPUT is required")
    with Path(output).open("a", encoding="utf-8") as handle:
        handle.write(f"release_tag={tag}\n")
    print(f"Release tag: {tag}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
