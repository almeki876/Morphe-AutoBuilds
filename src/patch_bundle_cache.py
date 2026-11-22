"""Durable cache in a hidden draft Release for patch bundles (.mpp, .rvp, .jar)."""

from __future__ import annotations

import logging
import mimetypes
import os
import subprocess
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

try:
    from github import Auth, Github
    from github.GithubException import GithubException, UnknownObjectException
except ImportError:
    Auth = None
    Github = None
    GithubException = Exception
    UnknownObjectException = Exception

CACHE_TAG = os.getenv("PATCH_BUNDLE_CACHE_TAG", "patch-bundle-cache-v1")
CACHE_RELEASE_NAME = "Automated patch bundle cache"
CACHE_RELEASE_MSG = (
    "Workflow-managed patch bundle cache. This draft release is "
    "intentionally hidden and must not be published manually."
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")


def is_patch_bundle_file(name: str) -> bool:
    """Check if a filename is a patch bundle or patch metadata file."""
    norm = name.casefold()
    return (
        norm.endswith((".mpp", ".rvp"))
        or (norm.endswith(".jar") and "patch" in norm)
        or norm == "patches-list.json"
    )


def is_cacheable_tool_file(name: str) -> bool:
    """Check if a filename in tools/<source>/ should be preserved in cache."""
    norm = name.casefold()
    if is_patch_bundle_file(name):
        return True
    # Also preserve CLI jar so complete offline toolchain is intact
    if norm.endswith(".jar") and not ("sources" in norm or "javadoc" in norm):
        return True
    return False


def parse_asset_name(asset_name: str) -> tuple[str, str, str] | None:
    """Parse `<source_name>--<tag>--<original_filename>`."""
    parts = asset_name.split("--", 2)
    if len(parts) == 3 and all(parts):
        return parts[0], parts[1], parts[2]
    return None


def make_asset_name(source: str, tag: str, filename: str) -> str:
    """Construct `<source_name>--<tag>--<original_filename>`."""
    return f"{source}--{tag}--{filename}"


def _get_github(token: str | None = None) -> Any:
    tok = (token or os.environ.get("GITHUB_TOKEN", "")).strip()
    if not tok or Github is None:
        return None
    return Github(auth=Auth.Token(tok)) if Auth else Github(tok)


def find_cache_release(repo: Any, create: bool = False) -> Any:
    """Find or optionally create the hidden draft cache release."""
    try:
        for release in repo.get_releases():
            if release.tag_name == CACHE_TAG:
                return release
    except (GithubException, UnknownObjectException) as e:
        logging.warning("Could not list releases: %s", e)
        return None

    if create:
        try:
            release = repo.create_git_release(
                tag=CACHE_TAG,
                name=CACHE_RELEASE_NAME,
                message=CACHE_RELEASE_MSG,
                draft=True,
                prerelease=False,
            )
            logging.info("Created draft patch bundle cache release %s", CACHE_TAG)
            return release
        except GithubException as e:
            logging.error("Failed to create draft cache release %s: %s", CACHE_TAG, e)
            return None
    return None


def get_cached_bundle_tags(repository: str | None = None, token: str | None = None) -> dict[str, str]:
    """Return `{source_name: tag}` for all sources present in the draft cache release."""
    repo_slug = (repository or os.environ.get("GITHUB_REPOSITORY", "")).strip()
    tok = (token or os.environ.get("GITHUB_TOKEN", "")).strip()

    # Try gh CLI first if available (fast and runner-native)
    if repo_slug:
        try:
            env = dict(os.environ)
            if tok:
                env["GH_TOKEN"] = tok
            cmd = [
                "gh", "release", "view", CACHE_TAG,
                "--repo", repo_slug,
                "--json", "assets",
                "--jq", ".assets.[].name",
            ]
            result = subprocess.run(cmd, env=env, capture_output=True, text=True, check=False)
            if result.returncode == 0 and result.stdout:
                cached_tags: dict[str, str] = {}
                for line in result.stdout.splitlines():
                    parsed = parse_asset_name(line.strip())
                    if parsed:
                        source, tag, _ = parsed
                        cached_tags[source] = tag
                return cached_tags
        except Exception as e:
            logging.debug("gh release view failed: %s", e)

    # PyGithub fallback
    gh = _get_github(tok)
    if gh and repo_slug:
        try:
            repo = gh.get_repo(repo_slug)
            rel = find_cache_release(repo, create=False)
            if rel:
                cached_tags = {}
                for asset in rel.get_assets():
                    parsed = parse_asset_name(asset.name)
                    if parsed:
                        source, tag, _ = parsed
                        cached_tags[source] = tag
                return cached_tags
        except Exception as e:
            logging.warning("PyGithub get_cached_bundle_tags failed: %s", e)

    return {}


def restore_cached_bundle(
    source_name: str,
    dest_dir: Path,
    repository: str | None = None,
    token: str | None = None,
) -> list[str]:
    """Restore all cached tool/patch files for source_name from the draft release."""
    repo_slug = (repository or os.environ.get("GITHUB_REPOSITORY", "")).strip()
    tok = (token or os.environ.get("GITHUB_TOKEN", "")).strip()
    restored: list[str] = []

    dest_dir.mkdir(parents=True, exist_ok=True)

    # Try gh release download first
    if repo_slug:
        try:
            env = dict(os.environ)
            if tok:
                env["GH_TOKEN"] = tok
            cmd = [
                "gh", "release", "download", CACHE_TAG,
                "--repo", repo_slug,
                "--pattern", f"{source_name}--*",
                "--dir", str(dest_dir),
                "--clobber",
            ]
            result = subprocess.run(cmd, env=env, capture_output=True, text=True, check=False)
            if result.returncode == 0:
                for downloaded in dest_dir.glob(f"{source_name}--*"):
                    parsed = parse_asset_name(downloaded.name)
                    if parsed:
                        _, _, orig_filename = parsed
                        target = dest_dir / orig_filename
                        downloaded.replace(target)
                        restored.append(orig_filename)
                        logging.info("  ⚡ Restored %s from draft release cache -> %s", orig_filename, target)
                if restored:
                    return restored
        except Exception as e:
            logging.debug("gh release download fallback error: %s", e)

    # PyGithub fallback
    gh = _get_github(tok)
    if gh and repo_slug:
        try:
            repo = gh.get_repo(repo_slug)
            rel = find_cache_release(repo, create=False)
            if rel:
                for asset in rel.get_assets():
                    parsed = parse_asset_name(asset.name)
                    if not parsed or parsed[0] != source_name:
                        continue
                    _, _, orig_filename = parsed
                    target = dest_dir / orig_filename
                    # Use curl to download the asset
                    headers = ["-H", "Accept: application/octet-stream"]
                    if tok:
                        headers.extend(["-H", f"Authorization: token {tok}"])
                    cmd = ["curl", "-fsSL", "--retry", "3", *headers, asset.url, "-o", str(target)]
                    if subprocess.run(cmd, capture_output=True, check=False).returncode == 0:
                        restored.append(orig_filename)
                        logging.info("  ⚡ Downloaded %s from draft release -> %s", orig_filename, target)
        except Exception as e:
            logging.warning("PyGithub restore_cached_bundle failed: %s", e)

    return restored


def upload_with_retry(release: Any, path: Path, asset_name: str) -> bool:
    """Upload asset to GitHub release with retries."""
    content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    for attempt in range(1, 4):
        try:
            release.upload_asset(
                str(path),
                label=asset_name,
                content_type=content_type,
                name=asset_name,
            )
            return True
        except GithubException as e:
            if attempt == 3:
                logging.error("Failed to upload %s after 3 attempts: %s", asset_name, e)
                return False
            time.sleep(5 * attempt)
    return False
