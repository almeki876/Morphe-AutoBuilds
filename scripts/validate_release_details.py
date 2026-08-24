"""Validate release-detail inputs before public indexing.

Most apps are single-source and can be represented directly from their actual
build report. Gboard is intentionally different: one successful APK must include
Jason plus the conflict-reviewed Adobo and Morning-Entree additions. This check
prevents a release-details page from claiming the three-source integration when
a supplemental patch silently disappeared.
"""

from __future__ import annotations

import json
import os
from pathlib import Path


BUILD_ROOT = Path(os.getenv("BUILD_RESULT_ROOT", "details-input/build"))
PATCH_CONFIG = Path(os.getenv("PATCH_CONFIG_PATH", "my-patch-config.json"))

GBOARD_EXTRA_SOURCES = ("adobo", "morning-entree")
CONFLICT_SUPPRESSED = {
    "adobo": {
        "Enable OCR feature",
        "Enable access points menu redesign",
        "Enable key shape selection",
    },
    "morning-entree": {"Change package name"},
}


def _json(path: Path) -> object | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _reports() -> list[dict]:
    reports: list[dict] = []
    for path in BUILD_ROOT.rglob("*.json") if BUILD_ROOT.exists() else ():
        payload = _json(path)
        if isinstance(payload, dict) and payload.get("app_name") and payload.get("source"):
            reports.append(payload)
    return reports


def _gboard_expected() -> dict[str, set[str]]:
    payload = _json(PATCH_CONFIG)
    if not isinstance(payload, dict):
        raise RuntimeError("my-patch-config.json is unavailable")
    result: dict[str, set[str]] = {}
    entries = payload.get("patch_list") or []
    for source in GBOARD_EXTRA_SOURCES:
        configured: set[str] | None = None
        for item in entries:
            if not isinstance(item, dict):
                continue
            if item.get("app_name") == "gboard" and item.get("source") == source:
                configured = {
                    str(name)
                    for name in (item.get("force_enable") or [])
                    if str(name).strip()
                }
                break
        if configured is None:
            raise RuntimeError(f"Gboard config is missing supplemental source {source}")
        result[source] = configured - CONFLICT_SUPPRESSED.get(source, set())
    return result


def validate() -> None:
    reports = _reports()
    successful = [report for report in reports if report.get("status") == "success"]
    if not successful:
        raise RuntimeError("No successful build reports were downloaded")

    gboard_reports = [
        report
        for report in successful
        if report.get("app_name") == "gboard"
    ]
    if not gboard_reports:
        return
    if len(gboard_reports) != 1:
        raise RuntimeError(
            f"Expected one integrated Gboard report, found {len(gboard_reports)}"
        )

    report = gboard_reports[0]
    if report.get("source") != "jason":
        raise RuntimeError(
            "Integrated Gboard build must use Jason as the primary matrix source"
        )

    applied = {str(name) for name in (report.get("applied_patches") or [])}
    expected = _gboard_expected()
    missing: list[str] = []
    for source, names in expected.items():
        for name in sorted(names):
            if name not in applied:
                missing.append(f"{source}: {name}")

    if missing:
        raise RuntimeError(
            "Gboard three-source verification failed; supplemental patch(es) "
            "were not reported as Applied: " + ", ".join(missing)
        )

    print(
        "Gboard three-source verification passed: "
        + "; ".join(
            f"{source}={','.join(sorted(names)) or '(none)'}"
            for source, names in expected.items()
        )
    )


def main() -> None:
    validate()


if __name__ == "__main__":
    main()
