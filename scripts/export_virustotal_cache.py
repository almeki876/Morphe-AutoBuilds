"""Export the portable VirusTotal SHA cache from a scan report.

The workflow owns artifact transport and release upload. This module owns only
report validation and deterministic cache serialization so it can be tested
without GitHub Actions.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


CACHE_VERSION = 1


def portable_cache(payload: dict[str, Any]) -> dict[str, Any]:
    cache = payload.get("cache")
    if not isinstance(cache, dict) or cache.get("version") != CACHE_VERSION:
        raise ValueError("VirusTotal report does not contain a v1 portable cache")
    results = cache.get("results")
    if not isinstance(results, dict):
        raise ValueError("VirusTotal portable cache has no results map")
    return cache


def export_cache(report_path: Path, output_path: Path) -> int:
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("VirusTotal report root must be a JSON object")
    cache = portable_cache(payload)
    output_path.write_text(
        json.dumps(cache, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    return len(cache["results"])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("report", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()

    count = export_cache(args.report, args.output)
    print(f"Prepared {count} cached SHA-256 result(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
