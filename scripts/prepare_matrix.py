"""Build the app/source matrix from the current repository configuration.

Selection is intentionally generic: patch-source changes arrive through
``UPDATED_SOURCES`` and base-APK changes through ``UPDATED_APPS``.  This keeps
workflow inputs in sync with ``sources/*.json`` without a hand-maintained list
of per-source environment flags.
"""

from __future__ import annotations

import json
import os
import pathlib
import sys


SOURCE_LABELS = {
    "morphe": "Morphe",
    "revanced-anddea": "Anddea",
    "rushiranpise": "rushiranpise",
    "hoomans": "arandomhooman",
    "rookie": "RookieEnough",
    "durgesh0505": "durgesh0505",
    "icysymmetra": "icysymmetra",
    "ajstrick81": "ajstrick81",
    "andrewliang25": "andrewliang25",
    "hoo-dles": "hoo-dles",
    "fluffy": "rabilrbl",
    "quantro": "Quantro100",
    "lain": "kiraio-moe",
    "jason": "jasonwu1994",
    "adobo": "jkennethcarino",
    "morning-entree": "Entree3k",
    "bholey": "BholeyKaBhakt",
    "paresh": "Paresh-Maheshwari",
    "dh6k": "dh6k",
    "shaun-the-sheep-patches": "shaun-the-sheep-patches",
    "hxreborn": "hxreborn",
    "nekogryphou": "NekoGryphou",
}

GBOARD_SOURCES = ("jason", "adobo", "morning-entree")
GBOARD_SOURCE_LABEL = "jasonwu1994 + jkennethcarino + Entree3k"


def _csv(name: str) -> set[str]:
    return {
        value.strip()
        for value in os.environ.get(name, "").split(",")
        if value.strip()
    }


def _enabled(item: dict) -> bool:
    return item.get("enabled", True) is not False and item.get("skip_build", False) is not True


def _collapse_gboard(items: list[dict], all_items: list[dict]) -> list[dict]:
    selected = [
        item
        for item in items
        if item.get("app_name") == "gboard" and item.get("source") in GBOARD_SOURCES
    ]
    if not selected:
        return items

    jason = next(
        (
            item
            for item in all_items
            if item.get("app_name") == "gboard" and item.get("source") == "jason"
        ),
        None,
    )
    if jason is None:
        raise RuntimeError("Gboard multi-source build requires the jason config entry")

    integrated = dict(jason)
    integrated["patch_sources"] = list(GBOARD_SOURCES)
    integrated["source_label"] = GBOARD_SOURCE_LABEL
    return [
        item
        for item in items
        if not (
            item.get("app_name") == "gboard"
            and item.get("source") in GBOARD_SOURCES
        )
    ] + [integrated]


def main() -> int:
    config_path = pathlib.Path("my-patch-config.json")
    data = json.loads(config_path.read_text(encoding="utf-8"))
    all_items = [item for item in data.get("patch_list", []) if isinstance(item, dict)]
    enabled_items = [item for item in all_items if _enabled(item)]

    build_all = os.environ.get("BUILD_ALL_SOURCES", "false").strip().lower() == "true"
    updated_sources = _csv("UPDATED_SOURCES")
    updated_apps = _csv("UPDATED_APPS")

    # The public workflow name uses "anddea" while the source declaration uses
    # the historical internal id "revanced-anddea".
    if "anddea" in updated_sources:
        updated_sources.add("revanced-anddea")

    if build_all:
        matrix = enabled_items
    elif updated_sources or updated_apps:
        # Source and base-APK updates can happen in the same scheduled check.
        # Build the union so neither class of change masks the other.
        matrix = [
            item
            for item in enabled_items
            if item.get("source") in updated_sources
            or item.get("app_name") in updated_apps
        ]
    else:
        matrix = []

    matrix = _collapse_gboard(matrix, all_items)
    for item in matrix:
        source = str(item.get("source", ""))
        item.setdefault("source_label", SOURCE_LABELS.get(source, source))

    if not matrix:
        print(
            "WARNING: No sources or apps were selected. Use build_all_sources, "
            "updated_sources, or updated_apps.",
            file=sys.stderr,
        )

    output_path = os.environ.get("GITHUB_OUTPUT", "").strip()
    payload = json.dumps(matrix, ensure_ascii=False, separators=(",", ":"))
    if output_path:
        with open(output_path, "a", encoding="utf-8") as output:
            output.write(f"matrix={payload}\n")
    else:
        print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
