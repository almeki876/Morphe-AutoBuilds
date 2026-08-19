"""Persist upstream state only after the complete build and release succeeds."""

from __future__ import annotations

import json
import os
from pathlib import Path
import runpy


STATE_FILE = Path("last-tags.json")
SOURCE_ENV = {
    "morphe": "SOURCE_TAG_MORPHE",
    "anddea": "SOURCE_TAG_ANDDEA",
    "rushiranpise": "SOURCE_TAG_RUSHIRANPISE",
    "rookie": "SOURCE_TAG_ROOKIE",
    "tosox": "SOURCE_TAG_TOSOX",
    "yuzu": "SOURCE_TAG_YUZU",
    "dropped": "SOURCE_TAG_DROPPED",
}


def main() -> None:
    try:
        state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        state = {}
    if not isinstance(state, dict):
        state = {}

    for key, env_name in SOURCE_ENV.items():
        value = os.getenv(env_name, "").strip()
        if not value and key == "anddea":
            value = os.getenv("SOURCE_TAG_REVANCED_ANDDEA", "").strip()
        if value and value not in {"latest", "unknown"} and not value.startswith("{"):
            state[key] = value

    STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    # The existing script resolves every current APK version while preserving
    # prior values for providers that are temporarily unavailable.
    runpy.run_path("scripts/save_apk_versions.py", run_name="__main__")


if __name__ == "__main__":
    main()
