"""Persistence for base-APK source metadata used in release details."""

from __future__ import annotations

import atexit
import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from src.providers import source_details


METADATA_PATH = Path("build-metadata") / "apk-sources.json"
SHARED_ORIGIN_PATH = Path("base-apk-input") / "origin.json"
_PENDING_ENTRIES: list[dict] = []


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


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        return ""
    return digest.hexdigest()


def _origin_url(provider: str, config: dict | None, provider_url: str) -> str:
    config = config or {}
    for key in (
        "download_url",
        "direct_url",
        "release_url",
        "page_url",
        "web_url",
        "url",
    ):
        value = config.get(key)
        if isinstance(value, str) and value.startswith(("https://", "http://")):
            return value

    package = str(config.get("package") or "").strip()
    if provider in {"aurora-google-play", "google-play"} and package:
        return f"https://play.google.com/store/apps/details?id={package}"
    if provider == "apkmirror" and package:
        return (
            "https://www.apkmirror.com/?post_type=app_release&searchtype=apk&s="
            + package
        )
    return provider_url


def _cache_sidecars(package: str, version: str) -> list[Path]:
    if not package or not version:
        return []
    try:
        from src import apk_cache

        matches: list[Path] = []
        if not apk_cache.CACHE_DIR.is_dir():
            return []
        for candidate in apk_cache.CACHE_DIR.iterdir():
            if not candidate.is_file() or not candidate.name.endswith(".origin.json"):
                continue
            asset_name = candidate.name.removesuffix(".origin.json")
            parsed = apk_cache.parse_asset_name(asset_name)
            if parsed and parsed[0] == package and parsed[1] == version:
                matches.append(candidate)
        return sorted(matches, key=lambda path: path.stat().st_mtime_ns, reverse=True)
    except Exception:
        return []


def _load_cached_origin(package: str, version: str) -> dict | None:
    for path in _cache_sidecars(package, version):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict):
            return payload
    return None


def _write_cache_origin(package: str, version: str, entry: dict) -> None:
    """Attach origin metadata to every newly staged cache asset.

    The sidecar is uploaded with the durable cache and downloaded by the same
    package/version prefix, so a future cache hit can still report the original
    Google Play/APKMirror/Uptodown/etc. origin instead of just saying "cache".
    """
    if not package or not version:
        return
    try:
        from src import apk_cache

        if not apk_cache.CACHE_DIR.is_dir():
            return
        for asset in apk_cache.CACHE_DIR.iterdir():
            if not asset.is_file() or asset.name.endswith(".origin.json"):
                continue
            parsed = apk_cache.parse_asset_name(asset.name)
            if not parsed or parsed[0] != package or parsed[1] != version:
                continue
            sidecar = asset.with_name(asset.name + ".origin.json")
            sidecar.write_text(
                json.dumps(entry, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
    except Exception as error:
        logging.warning("Could not persist APK cache origin sidecar: %s", error)


def _write_shared_origin(entry: dict) -> None:
    """Persist provenance next to the shared base APK artifact."""
    try:
        SHARED_ORIGIN_PATH.parent.mkdir(parents=True, exist_ok=True)
        SHARED_ORIGIN_PATH.write_text(
            json.dumps(entry, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    except OSError as error:
        logging.warning("Could not persist shared APK origin metadata: %s", error)


def _merge_entry(entries: list[dict], entry: dict) -> list[dict]:
    key_fields = ("app_name", "patch_source", "version", "architecture")
    retained = [
        item
        for item in entries
        if not all(item.get(field) == entry.get(field) for field in key_fields)
    ]
    retained.append(entry)
    return retained


def _flush_pending_entries() -> None:
    """Reassert provenance after download orchestration finishes.

    Older download orchestration rewrites ``apk-sources.json`` after the
    provider has already recorded provenance.  Keeping a pending copy and
    flushing it at interpreter shutdown guarantees the artifact uploaded by the
    workflow still contains the download-time provenance.  The dedicated
    ``base-apk-input/origin.json`` remains the immutable per-download copy.
    """
    if not _PENDING_ENTRIES:
        return
    try:
        entries = _read_entries()
        for entry in _PENDING_ENTRIES:
            entries = _merge_entry(entries, entry)
        _write_entries(entries)
    except OSError as error:
        logging.warning("Could not finalize APK provenance metadata: %s", error)


atexit.register(_flush_pending_entries)


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
    """Atomically record which provider originally supplied a build's base APK."""
    config = dict(config or {})
    package = str(config.get("package") or "").strip()
    label, provider_url = source_details(provider, config)

    original_provider = provider
    original_label = label
    original_provider_url = provider_url
    original_origin_url = _origin_url(provider, config, provider_url)
    legacy_cache_origin = False
    cache_sidecar_found = False

    is_cached = bool(cached or provider == "cache")
    if is_cached:
        cached_origin = _load_cached_origin(package, version)
        if cached_origin:
            cache_sidecar_found = True
            original_provider = str(cached_origin.get("provider") or provider)
            original_label = str(cached_origin.get("provider_label") or label)
            original_provider_url = str(
                cached_origin.get("provider_url") or provider_url or ""
            )
            original_origin_url = str(
                cached_origin.get("origin_url") or original_provider_url or ""
            )
        else:
            legacy_cache_origin = True

    entry = {
        "app_name": app_name,
        "patch_source": os.getenv("SOURCE", ""),
        "package": package or None,
        "version": version,
        "architecture": arch or "universal",
        "provider": original_provider,
        "provider_label": original_label,
        "provider_url": original_provider_url,
        "origin_url": original_origin_url,
        "retrieval_method": "cache_restore" if is_cached else "direct_provider",
        "cached": is_cached,
        "cache_provider": "GitHub Base APK Cache" if is_cached else None,
        "cache_tag": os.getenv("BASE_APK_CACHE_TAG", "base-apk-cache-v2")
        if is_cached
        else None,
        "cache_origin_sidecar_found": cache_sidecar_found,
        "legacy_cache_origin_unknown": legacy_cache_origin,
        "filename": filepath.name,
        "sha256": _sha256(filepath),
        "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    entries = _merge_entry(_read_entries(), entry)
    _write_entries(entries)
    _write_shared_origin(entry)
    _PENDING_ENTRIES[:] = _merge_entry(_PENDING_ENTRIES, entry)

    # Directly downloaded APKs may already have been staged locally.  The cache
    # promotion workflow also copies ``origin.json`` next to newly staged cache
    # assets, so the provenance survives regardless of which staging path wins.
    if not entry["cached"]:
        _write_cache_origin(package, version, entry)

    logging.info(
        "🧾 Recorded base APK source: %s %s -> %s%s",
        app_name,
        version,
        original_label,
        " (restored from cache)" if entry["cached"] else "",
    )


def record_failure(
    app_name: str,
    source: str,
    arch: str,
    category: str,
    message: str,
) -> None:
    """Persist a failed build classification for release reporting."""
    entry = {
        "app_name": app_name,
        "patch_source": source,
        "version": "unknown",
        "architecture": arch,
        "provider": None,
        "provider_label": None,
        "provider_url": None,
        "origin_url": None,
        "retrieval_method": None,
        "cached": False,
        "cache_provider": None,
        "cache_tag": None,
        "cache_origin_sidecar_found": False,
        "legacy_cache_origin_unknown": False,
        "filename": None,
        "sha256": None,
        "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "build_status": "failure",
        "error_category": category,
        "error_summary": message,
    }
    key_fields = ("app_name", "patch_source", "architecture")
    entries = [
        item
        for item in _read_entries()
        if not all(item.get(field) == entry[field] for field in key_fields)
    ]
    entries.append(entry)
    try:
        _write_entries(entries)
    except OSError as error:
        logging.warning(
            "Could not record failed build metadata for %s/%s: %s",
            app_name,
            arch,
            error,
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
