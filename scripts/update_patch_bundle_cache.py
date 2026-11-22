"""Upload latest patch bundles (.mpp, .rvp, .jar) to a hidden draft Release.

This ensures that even if an upstream patch repository is later deleted or
blocked by GitHub TOS, the build workflow retains an authoritative copy of
the latest patch bundle to continue builds uninterrupted.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from collections import defaultdict
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from src.patch_bundle_cache import (
    CACHE_TAG,
    _get_github,
    find_cache_release,
    is_cacheable_tool_file,
    make_asset_name,
    parse_asset_name,
    upload_with_retry,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
TOOLS_DIR = Path(os.getenv("TOOLS_DIR", "tools"))
STATE_FILE = Path("last-tags.json")


def _read_last_tags() -> dict[str, str]:
    if not STATE_FILE.is_file():
        return {}
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        return {k: str(v) for k, v in data.items() if isinstance(v, str)}
    except Exception:
        return {}


def main() -> int:
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    repository = os.environ.get("GITHUB_REPOSITORY", "").strip()
    if not token or not repository:
        logging.warning("GITHUB_TOKEN or GITHUB_REPOSITORY not set; skipping patch bundle cache update")
        return 0

    gh = _get_github(token)
    if not gh:
        logging.warning("PyGithub not available; skipping patch bundle cache update")
        return 0

    try:
        repo = gh.get_repo(repository)
    except Exception as e:
        logging.warning("Could not access repository %s: %e", repository, e)
        return 0

    release = find_cache_release(repo, create=True)
    if not release:
        logging.warning("Could not obtain draft cache release; skipping patch bundle cache update")
        return 0

    # Determine tags for sources
    try:
        resolved_tags = json.loads(os.environ.get("SOURCE_TAGS_JSON", "{}"))
    except Exception:
        resolved_tags = {}
    last_tags = _read_last_tags()

    existing_assets = {asset.name: asset for asset in release.get_assets()}
    uploaded_count = 0

    # Assets grouped by source: source -> list of (asset_name, tag, asset_obj)
    assets_by_source: dict[str, list[tuple[str, str, Any]]] = defaultdict(list)
    for asset in existing_assets.values():
        parsed = parse_asset_name(asset.name)
        if parsed:
            src, tag, _ = parsed
            assets_by_source[src].append((asset.name, tag, asset))

    if not TOOLS_DIR.is_dir():
        logging.info("Tools directory %s does not exist; nothing to upload", TOOLS_DIR)
        return 0

    for source_dir in sorted(TOOLS_DIR.iterdir()):
        if not source_dir.is_dir():
            continue
        source_name = source_dir.name
        tag = (
            resolved_tags.get(source_name)
            or last_tags.get(source_name)
            or ""
        ).strip()
        if not tag:
            # Fallback: check if any file has version or use "latest"
            tag = "latest"

        # Find files to cache
        for file_path in source_dir.iterdir():
            if not file_path.is_file() or not is_cacheable_tool_file(file_path.name):
                continue

            asset_name = make_asset_name(source_name, tag, file_path.name)
            if asset_name in existing_assets:
                logging.info("  ⏭️  Already cached in draft release: %s", asset_name)
                continue

            logging.info("  ⬆️  Uploading patch bundle to draft release: %s", asset_name)
            if upload_with_retry(release, file_path, asset_name):
                uploaded_count += 1
                assets_by_source[source_name].append((asset_name, tag, None))

    # Prune older tags for each source so only the newest version remains in draft release
    for source_name, source_assets in assets_by_source.items():
        # Find the newest tag among assets
        current_tag = (
            resolved_tags.get(source_name)
            or last_tags.get(source_name)
            or ""
        ).strip()
        if not current_tag:
            continue

        for asset_name, asset_tag, asset_obj in source_assets:
            if asset_tag != current_tag and asset_obj is not None:
                try:
                    asset_obj.delete_asset()
                    logging.info("  🗑️  Pruned outdated cached bundle: %s", asset_name)
                except Exception as e:
                    logging.warning("Failed to prune %s: %s", asset_name, e)

    logging.info("✅ Patch bundle cache update complete (%d new asset(s) uploaded).", uploaded_count)
    return 0


if __name__ == "__main__":
    sys.exit(main())
