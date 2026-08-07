"""
GitHub Releases downloader for APKs.

Config format (apps/github/{app_name}.json):
{
    "user": "AdguardTeam",
    "repo": "AdguardForAndroid",
    "asset_pattern": "adguard-{version}.apk",
    "package": "com.adguard.android",
    "version": ""
}

asset_pattern supports:
  - "{version}"  → replaced with the resolved version string
  - "*"          → matches any single segment (glob-style, matched in order)
  - Literal filename suffix matching (e.g. ".apk")

If version is empty, the latest release tag is used and the first matching
.apk asset is downloaded.
"""

import fnmatch
import logging
import re
from typing import Dict, Optional

from src import utils


def _resolve_release(config: Dict) -> tuple[dict, str]:
    """Return (release_raw_data, version_string)."""
    user = config["user"]
    repo = config["repo"]
    tag = config.get("tag", "latest")
    release = utils.detect_github_release(
        user,
        repo,
        tag,
        include_prereleases=config.get("include_prereleases", True),
    )
    # Extract clean version from tag (strip leading 'v')
    tag_name = release.get("tag_name", "")
    version = re.sub(r"^v", "", tag_name)
    return release, version


def _matching_asset(
    release: dict, config: Dict, app_name: str, version: str
) -> Optional[dict]:
    """Select one APK asset without crossing an explicit app pattern."""
    assets = release.get("assets", [])
    asset_pattern = config.get("asset_pattern", "*.apk")
    asset_exclude = config.get("asset_exclude", "")
    pattern = asset_pattern.replace("{version}", version)

    for asset in assets:
        name = str(asset.get("name", ""))
        if (
            fnmatch.fnmatch(name, pattern)
            and name.lower().endswith(".apk")
            and not (asset_exclude and asset_exclude in name)
        ):
            return asset

    # An explicit pattern identifies a particular app/variant. Falling back to
    # another APK in the same release can download the wrong application.
    if asset_pattern != "*.apk":
        logging.error(
            "github: no asset matched explicit pattern %r for %s",
            pattern,
            app_name,
        )
        return None

    for asset in assets:
        name = str(asset.get("name", ""))
        if name.lower().endswith(".apk") and not (
            asset_exclude and asset_exclude in name
        ):
            return asset
    return None


def _asset_version(asset: dict, config: Dict) -> Optional[str]:
    pattern = config.get("version_pattern")
    if not pattern:
        return None
    match = re.search(str(pattern), str(asset.get("name", "")))
    if not match:
        return None
    if "version" in match.groupdict():
        return match.group("version")
    if match.groups():
        return match.group(1)
    logging.error("github: version_pattern must contain a capture group")
    return None


def get_latest_version(app_name: str, config: Dict) -> Optional[str]:
    # direct_url configs have no user/repo — return the pinned version if set,
    # otherwise return None so the caller falls through to the next platform.
    if "direct_url" in config:
        version = config.get("version") or None
        if version:
            logging.info(f"github: direct_url version for {app_name} is {version}")
        else:
            logging.info(f"github: direct_url config for {app_name} has no version, skipping")
        return version
    try:
        release, version = _resolve_release(config)
        if config.get("version_pattern"):
            asset = _matching_asset(release, config, app_name, version)
            if not asset:
                return None
            asset_version = _asset_version(asset, config)
            if not asset_version:
                logging.error(
                    "github: could not extract app version from asset %r for %s",
                    asset.get("name"),
                    app_name,
                )
                return None
            version = asset_version
        logging.info(f"github: latest version for {app_name} is {version}")
        return version
    except Exception as e:
        logging.error(f"github: failed to resolve version for {app_name}: {e}")
        return None


def get_download_link(version: str, app_name: str, config: Dict) -> Optional[str]:
    try:
        release, _ = _resolve_release(config)
        asset = _matching_asset(release, config, app_name, version)
        if asset:
            name = asset["name"]
            logging.info(f"github: matched asset '{name}' for {app_name} v{version}")
            return asset["browser_download_url"]

        logging.error(f"github: no matching .apk asset found for {app_name}")
        return None

    except Exception as e:
        logging.error(f"github: failed to get download link for {app_name}: {e}")
        return None
