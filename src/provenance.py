"""Persistence for base-APK source metadata used in release notes."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from src.providers import source_details


METADATA_PATH = Path("build-metadata") / "apk-sources.json"


def _read_entries() -> list[dict]:
    try:
        data = json.loads(METADATA_PATH.read_text(encoding="utf-8"))
        return [item for item in data if isinstance(item, dict)]
    except (OSError, json.JSONDecodeError, TypeError):
        return []


def _write_entries(entries: list[dict]) -> None:
    METADATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = METADATA_PATH.with_suffix(".json.part")
    temporary.write_text(
        json.dumps(entries, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(METADATA_PATH)


def record(
    app_name: str,
    version: str,
    provider: str,
    filepath: Path,
    arch: str | None,
    *,
    cached: bool = False,
    config: dict | None = None,
) -> None:
    """Atomically record which provider supplied a build's base APK."""
    label, provider_url = source_details(provider, config)
    entry = {
        "app_name": app_name,
        "patch_source": os.getenv("SOURCE", ""),
        "version": version,
        "architecture": arch or "universal",
        "provider": provider,
        "provider_label": label,
        "provider_url": provider_url,
        "cached": cached,
        "filename": filepath.name,
    }
    key_fields = ("app_name", "patch_source", "version", "architecture")
    entries = [
        item
        for item in _read_entries()
        if not all(item.get(field) == entry[field] for field in key_fields)
    ]
    entries.append(entry)
    _write_entries(entries)
    logging.info(
        "🧾 Recorded base APK source: %s %s -> %s%s",
        app_name,
        version,
        label,
        " (cache)" if cached else "",
    )


def remove(app_name: str, arch: str) -> None:
    """Remove provenance for an architecture that failed to produce an APK."""
    if not METADATA_PATH.exists():
        return
    try:
        entries = [
            item
            for item in _read_entries()
            if not (
                item.get("app_name") == app_name
                and item.get("architecture") == arch
            )
        ]
        _write_entries(entries)
    except OSError as error:
        logging.warning(
            "Could not remove failed APK provenance for %s/%s: %s",
            app_name,
            arch,
            error,
        )
