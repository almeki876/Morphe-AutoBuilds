"""Durable, integrity-checked cache for original APK inputs.

The cache is stored as assets on a draft GitHub Release.  Draft releases are
visible to the workflow token but are not exposed on the repository's public
Releases page.  A successfully used original is staged during the build and a
single workflow job uploads it after the matrix finishes.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import os
import re
import shutil
import zipfile
from pathlib import Path

from github import Github
from github.GithubException import GithubException, UnknownObjectException

CACHE_TAG = os.getenv("BASE_APK_CACHE_TAG", "base-apk-cache-v1")
CACHE_DIR = Path(os.getenv("BASE_APK_CACHE_DIR", "base-apk-cache-out"))
_ASSET_RE = re.compile(
    r"^baseapk-v1--p_([A-Za-z0-9_-]+)--v_([A-Za-z0-9_-]+)"
    r"--([0-9a-f]{64})(\.(?:apk|apkm|apks|xapk|zip))$",
    re.IGNORECASE,
)
_REMOTE_MISSES: set[tuple[str, str]] = set()


def _enabled() -> bool:
    return os.getenv("BASE_APK_CACHE", "true").lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


def _encode(value: str) -> str:
    return base64.urlsafe_b64encode(value.encode("utf-8")).decode("ascii").rstrip("=")


def _decode(value: str) -> str:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)).decode("utf-8")


def parse_asset_name(name: str) -> tuple[str, str, str, str] | None:
    match = _ASSET_RE.fullmatch(name)
    if not match:
        return None
    try:
        package = _decode(match.group(1))
        version = _decode(match.group(2))
    except (ValueError, UnicodeDecodeError):
        return None
    return package, version, match.group(3).lower(), match.group(4).lower()


def _asset_prefix(package: str, version: str) -> str:
    return f"baseapk-v1--p_{_encode(package)}--v_{_encode(version)}--"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate(path: Path, expected_sha256: str | None = None) -> bool:
    try:
        if not path.is_file() or path.stat().st_size <= 0:
            return False
        if not zipfile.is_zipfile(path):
            logging.warning("APK cache rejected a non-ZIP input: %s", path)
            return False
        if expected_sha256 and _sha256(path) != expected_sha256:
            logging.warning("APK cache SHA-256 mismatch: %s", path)
            return False
        return True
    except OSError as error:
        logging.warning("APK cache validation failed for %s: %s", path, error)
        return False


def validate_asset(path: Path) -> bool:
    parsed = parse_asset_name(path.name)
    return bool(parsed and _validate(path, parsed[2]))


def is_valid_apk_archive(path: Path) -> bool:
    """Return whether a provider result is a non-empty APK/split ZIP archive."""
    return _validate(path)


def find_release(repo, tag: str = CACHE_TAG):
    """Find a cache release, including drafts omitted by the tag endpoint."""
    try:
        return repo.get_release(tag)
    except UnknownObjectException:
        # GitHub's tag endpoint can omit draft releases. Authenticated release
        # listing still includes them, so search the small recent set as well.
        for release in repo.get_releases():
            if release.tag_name == tag:
                return release
    return None


def _copy_for_build(source: Path, app_name: str, version: str) -> Path:
    suffix = source.suffix.lower() or ".apk"
    safe_app = re.sub(r"[^A-Za-z0-9._-]+", "-", app_name).strip("-") or "app"
    safe_version = re.sub(r"[^A-Za-z0-9._-]+", "-", version).strip("-") or "unknown"
    target = Path(f"cached-{safe_app}-v{safe_version}{suffix}")
    temporary = target.with_name(f".{target.name}.part")
    shutil.copy2(source, temporary)
    temporary.replace(target)
    return target


def restore(package: str, version: str, app_name: str) -> Path | None:
    """Restore an exact package/version, preferring this job's local staging."""
    if not _enabled() or not package or not version:
        return None

    prefix = _asset_prefix(package, version)
    if CACHE_DIR.exists():
        for candidate in sorted(CACHE_DIR.glob(f"{prefix}*")):
            parsed = parse_asset_name(candidate.name)
            if parsed and validate_asset(candidate):
                restored = _copy_for_build(candidate, app_name, version)
                logging.info("📦 APK cache hit (local): %s %s", package, version)
                return restored

    key = (package, version)
    if key in _REMOTE_MISSES:
        return None

    token = os.getenv("GITHUB_TOKEN")
    repository = os.getenv("BASE_APK_CACHE_REPOSITORY") or os.getenv(
        "GITHUB_REPOSITORY"
    )
    if not token or not repository:
        _REMOTE_MISSES.add(key)
        logging.info("APK cache remote lookup disabled (token/repository unavailable)")
        return None

    try:
        release = find_release(Github(token).get_repo(repository))
        if release is None:
            _REMOTE_MISSES.add(key)
            logging.info("APK cache release does not exist yet; using providers")
            return None

        matches = [
            asset for asset in release.get_assets() if asset.name.startswith(prefix)
        ]
        matches.sort(
            key=lambda asset: asset.created_at or asset.updated_at,
            reverse=True,
        )
        for asset in matches:
            parsed = parse_asset_name(asset.name)
            if not parsed:
                continue
            headers = {
                "Accept": "application/octet-stream",
                "Authorization": f"Bearer {token}",
                "X-GitHub-Api-Version": "2022-11-28",
            }
            try:
                from src.downloader import download_resource

                candidate = download_resource(
                    asset.url,
                    name=asset.name,
                    headers=headers,
                )
                if not validate_asset(candidate):
                    candidate.unlink(missing_ok=True)
                    continue
                restored = _copy_for_build(candidate, app_name, version)
                candidate.unlink(missing_ok=True)
                logging.info("📦 APK cache hit (GitHub): %s %s", package, version)
                return restored
            except Exception as error:
                logging.warning("APK cache asset download failed: %s", error)

    except GithubException as error:
        logging.warning("APK cache lookup failed; using providers: %s", error)
    except Exception as error:
        logging.warning(
            "APK cache lookup failed unexpectedly; using providers: %s", error
        )

    _REMOTE_MISSES.add(key)
    logging.info("APK cache miss: %s %s", package, version)
    return None


def stage(path: Path, package: str, version: str, provider: str) -> Path | None:
    """Stage a provider download for upload after a successful build."""
    if not _enabled() or not package or not version or not _validate(path):
        return None

    digest = _sha256(path)
    suffix = path.suffix.lower()
    if suffix not in {".apk", ".apkm", ".apks", ".xapk", ".zip"}:
        suffix = ".apk"
    asset_name = f"{_asset_prefix(package, version)}{digest}{suffix}"
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    target = CACHE_DIR / asset_name
    if not target.exists():
        temporary = target.with_name(f".{target.name}.part")
        shutil.copy2(path, temporary)
        temporary.replace(target)
    logging.info(
        "📥 Staged verified APK cache candidate: %s %s (%s)",
        package,
        version,
        provider,
    )
    return target
