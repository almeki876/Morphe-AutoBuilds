"""Find app/source build targets that have no published APK yet.

``check_apk_versions.py`` only watches apps whose APK is not pinned by a patch
bundle, and ``check_upstream_sources.py`` only notices new *releases* of a
source it already tracks. Neither notices that a target was just added to
``my-patch-config.json`` (a new app) or moved to another source (for example
Poweramp switching patch authors), so such changes waited for a manual build.

The published catalog (``Morphe-AutoBuilds-Direct-Download.md``) is rewritten
after every successful release and lists the newest APK of every built
(app, source) pair. A configured pair that is missing from it has never been
built successfully, so it is handed to the normal build dispatch.

A target is only picked up when it is genuinely new: the app has no recorded
successful build (``apk_<app>`` in ``last-tags.json``), or the app is already in
the catalog under a different source (it was moved or gained a source). Apps
that were built but never appear in the catalog on purpose - Gboard's chained
three-source build is the known case - are therefore left alone.

Safety rails: if the catalog is missing, unreadable or lists nothing, or if too
many targets look unpublished (a truncated catalog would otherwise trigger a
full rebuild on every check), nothing is auto-built and a note explains why.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "my-patch-config.json"
CATALOG = ROOT / "Morphe-AutoBuilds-Direct-Download.md"
STATE = ROOT / "last-tags.json"

# Keep in sync with ASSET_RE in generate_direct_download_md.py (a test checks it).
ASSET_RE = re.compile(
    r"^(?P<app>.+?)-(?P<arch>universal|arm64-v8a|armeabi-v7a|x86_64|x86)-"
    r"(?P<source>.+)-v(?P<version>.+)\.apk$",
    re.IGNORECASE,
)
LINK_RE = re.compile(r"/releases/download/[^/\s)]+/([^/\s)]+\.apk)")

MAX_AUTO_TARGETS = 12


def is_enabled(item: dict) -> bool:
    """Same rule as prepare_matrix.is_enabled."""
    return item.get("enabled", True) is not False and item.get("skip_build", False) is not True


def published_targets(markdown: str) -> set[tuple[str, str]]:
    """(app, source) pairs that have a published APK link, lowercased."""
    pairs: set[tuple[str, str]] = set()
    for match in LINK_RE.finditer(markdown):
        parsed = ASSET_RE.match(match.group(1))
        if parsed:
            pairs.add((parsed.group("app").casefold(), parsed.group("source").casefold()))
    return pairs


def configured_targets(patch_list: list[dict]) -> list[tuple[str, str]]:
    seen: set[tuple[str, str]] = set()
    ordered: list[tuple[str, str]] = []
    for item in patch_list:
        if not is_enabled(item):
            continue
        pair = (str(item["app_name"]), str(item["source"]))
        if pair not in seen:
            seen.add(pair)
            ordered.append(pair)
    return ordered


def find_unpublished(
    patch_list: list[dict], markdown: str, state: dict
) -> list[tuple[str, str]]:
    published = published_targets(markdown)
    published_apps = {app for app, _ in published}
    missing: list[tuple[str, str]] = []
    for app, source in configured_targets(patch_list):
        if (app.casefold(), source.casefold()) in published:
            continue
        never_built = not state.get(f"apk_{app}")
        moved_or_extended = app.casefold() in published_apps
        if never_built or moved_or_extended:
            missing.append((app, source))
    return missing


def unpublished_apps(
    config_path: Path = CONFIG,
    catalog_path: Path = CATALOG,
    state_path: Path = STATE,
) -> tuple[list[str], list[str]]:
    """Return ``(apps_to_build, notes)``. Never raises; on doubt builds nothing."""
    try:
        patch_list = json.loads(config_path.read_text(encoding="utf-8"))["patch_list"]
        markdown = catalog_path.read_text(encoding="utf-8")
    except (OSError, ValueError, KeyError) as error:
        return [], [f"published catalog check skipped: {error}"]
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return [], ["last-tags.json unreadable; automatic new-target builds skipped"]
    if not isinstance(state, dict):
        return [], ["last-tags.json is not an object; automatic new-target builds skipped"]

    if not published_targets(markdown):
        return [], ["published catalog lists no APKs; automatic new-target builds skipped"]

    missing = find_unpublished(patch_list, markdown, state)
    if len(missing) > MAX_AUTO_TARGETS:
        return [], [
            f"{len(missing)} targets look unpublished (limit {MAX_AUTO_TARGETS}); "
            "refusing an automatic mass rebuild - use the full build workflow if intended"
        ]
    apps = sorted({app for app, _ in missing})
    notes = [f"{app} ({source}) has no published APK yet" for app, source in missing]
    return apps, notes
