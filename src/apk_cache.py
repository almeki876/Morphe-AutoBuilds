"""Durable, integrity-checked cache for original APK inputs."""

from __future__ import annotations

import base64
import hashlib
import logging
import os
import re
import shutil
import subprocess
import zipfile
from pathlib import Path

from github import Auth, Github
from github.GithubException import GithubException, UnknownObjectException
from src.apk_language import JapaneseResourceError, contains_japanese

CACHE_TAG = os.getenv("BASE_APK_CACHE_TAG", "base-apk-cache-v4-ja-verified")
CACHE_DIR = Path(os.getenv("BASE_APK_CACHE_DIR", "base-apk-cache-out"))
_ASSET_RE = re.compile(
    r"^baseapk-v2--p_([A-Za-z0-9_-]+)--v_([A-Za-z0-9_-]+)"
    r"--([0-9a-f]{64})--dp_([A-Za-z0-9_-]+)(\.(?:apk|apkm|apks|xapk|zip))$",
    re.IGNORECASE,
)
_REMOTE_MISSES: set[tuple[str, str]] = set()
GENERIC_PROFILE = "generic-v1"


def _enabled() -> bool:
    return os.getenv("BASE_APK_CACHE", "true").lower() not in {"0", "false", "no", "off"}


def _encode(value: str) -> str:
    return base64.urlsafe_b64encode(value.encode("utf-8")).decode("ascii").rstrip("=")


def _decode(value: str) -> str:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)).decode("utf-8")


def delivery_profile(provider: str) -> str:
    """Identify the provider without making provider choice a language gate."""
    normalized = re.sub(r"[^A-Za-z0-9_-]+", "-", provider.strip().lower()).strip("-")
    return f"{normalized or 'unknown'}-{GENERIC_PROFILE}"


def parse_asset_name(name: str) -> tuple[str, str, str, str] | None:
    match = _ASSET_RE.fullmatch(name)
    if not match:
        return None
    try:
        package = _decode(match.group(1))
        version = _decode(match.group(2))
    except (ValueError, UnicodeDecodeError):
        return None
    return package, version, match.group(3).lower(), match.group(5).lower()


def _asset_profile(name: str) -> str | None:
    match = _ASSET_RE.fullmatch(name)
    return match.group(4) if match else None


def _asset_prefix(package: str, version: str) -> str:
    return f"baseapk-v2--p_{_encode(package)}--v_{_encode(version)}--"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate(path: Path, expected_sha256: str | None = None) -> bool:
    try:
        if not path.is_file() or path.stat().st_size <= 0 or not zipfile.is_zipfile(path):
            return False
        with zipfile.ZipFile(path) as archive:
            names = {name.replace("\\", "/").lstrip("/") for name in archive.namelist()}
        is_apk = "AndroidManifest.xml" in names
        is_split_container = any(name.casefold().endswith(".apk") for name in names)
        if not is_apk and not is_split_container:
            logging.warning("APK cache rejected non-APK archive: %s", path)
            return False
        if expected_sha256 and _sha256(path) != expected_sha256:
            logging.warning("APK cache SHA-256 mismatch: %s", path)
            return False
        return True
    except (OSError, zipfile.BadZipFile) as error:
        logging.warning("APK cache validation failed for %s: %s", path, error)
        return False


def _contains_japanese(path: Path) -> bool:
    try:
        return contains_japanese(path)
    except JapaneseResourceError as error:
        logging.warning("APK rejected because Japanese resources were not proven: %s", error)
        return False


def validate_asset(path: Path) -> bool:
    parsed = parse_asset_name(path.name)
    return bool(parsed and _validate(path, parsed[2]) and _contains_japanese(path))


def is_valid_apk_archive(path: Path) -> bool:
    """Validate provider output and require verifiable Japanese resources."""
    return _validate(path) and _contains_japanese(path)


def find_release(repo, tag: str = CACHE_TAG):
    try:
        return repo.get_release(tag)
    except UnknownObjectException:
        for release in repo.get_releases():
            if release.tag_name == tag:
                return release
    return None


def _download_with_gh(repository: str, tag: str, prefix: str) -> list[Path]:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    pattern = f"{prefix}*"
    environment = os.environ.copy()
    token = environment.get("GITHUB_TOKEN") or environment.get("GH_TOKEN")
    if token:
        environment["GH_TOKEN"] = token
    try:
        result = subprocess.run(
            ["gh", "release", "download", tag, "--repo", repository, "--pattern", pattern,
             "--clobber", "--dir", str(CACHE_DIR)],
            capture_output=True, text=True, env=environment, check=False,
        )
    except OSError as error:
        logging.warning("gh release download could not start: %s", error)
        return []
    if result.returncode:
        logging.warning("gh release download failed (exit %d): %s", result.returncode, result.stderr.strip())
        return []
    return sorted(CACHE_DIR.glob(pattern))


def _copy_for_build(source: Path, app_name: str, version: str) -> Path:
    suffix = source.suffix.lower() or ".apk"
    safe_app = re.sub(r"[^A-Za-z0-9._-]+", "-", app_name).strip("-") or "app"
    safe_version = re.sub(r"[^A-Za-z0-9._-]+", "-", version).strip("-") or "unknown"
    target = Path(f"cached-{safe_app}-v{safe_version}{suffix}")
    temporary = target.with_name(f".{target.name}.part")
    shutil.copy2(source, temporary)
    temporary.replace(target)
    return target


def _candidate_sort_key(path: Path) -> int:
    try:
        return -path.stat().st_mtime_ns
    except OSError:
        return 0


def _usable_candidate(path: Path) -> bool:
    return bool(parse_asset_name(path.name) and validate_asset(path))


def restore(package: str, version: str, app_name: str) -> Path | None:
    """Restore only an exact, integrity-checked cache asset containing Japanese resources."""
    if not _enabled() or not package or not version:
        return None
    prefix = _asset_prefix(package, version)
    if CACHE_DIR.exists():
        for candidate in sorted(CACHE_DIR.glob(f"{prefix}*"), key=_candidate_sort_key):
            if _usable_candidate(candidate):
                restored = _copy_for_build(candidate, app_name, version)
                logging.info("📦 APK cache hit (local): %s %s profile=%s", package, version, _asset_profile(candidate.name))
                return restored

    key = (package, version)
    if key in _REMOTE_MISSES:
        return None
    token = os.getenv("GITHUB_TOKEN")
    repository = os.getenv("BASE_APK_CACHE_REPOSITORY") or os.getenv("GITHUB_REPOSITORY")
    if not token or not repository:
        _REMOTE_MISSES.add(key)
        return None

    try:
        release = find_release(Github(auth=Auth.Token(token)).get_repo(repository))
        if release is None:
            _REMOTE_MISSES.add(key)
            return None
        downloaded = _download_with_gh(repository, release.tag_name, prefix)
        for candidate in sorted(downloaded, key=_candidate_sort_key):
            if _usable_candidate(candidate):
                restored = _copy_for_build(candidate, app_name, version)
                logging.info("📦 APK cache hit (GitHub CLI): %s %s profile=%s", package, version, _asset_profile(candidate.name))
                return restored
        matches = [asset for asset in release.get_assets() if asset.name.startswith(prefix)]
        for asset in matches:
            if not _asset_profile(asset.name):
                continue
            try:
                from src.downloader import download_resource
                candidate = download_resource(
                    asset.url,
                    name=asset.name,
                    headers={
                        "Accept": "application/octet-stream",
                        "Authorization": f"Bearer {token}",
                        "X-GitHub-Api-Version": "2022-11-28",
                    },
                )
                if not _usable_candidate(candidate):
                    candidate.unlink(missing_ok=True)
                    continue
                restored = _copy_for_build(candidate, app_name, version)
                candidate.unlink(missing_ok=True)
                logging.info("📦 APK cache hit (GitHub): %s %s profile=%s", package, version, _asset_profile(asset.name))
                return restored
            except Exception as error:
                logging.warning("APK cache asset download failed: %s", error)
    except GithubException as error:
        logging.warning("APK cache lookup failed; using providers: %s", error)
    except Exception as error:
        logging.warning("APK cache lookup failed unexpectedly; using providers: %s", error)
    _REMOTE_MISSES.add(key)
    return None


def stage(path: Path, package: str, version: str, provider: str) -> Path | None:
    """Stage a provider APK only after proving it contains Japanese resources."""
    if not _enabled() or not package or not version or not _validate(path):
        return None
    if not _contains_japanese(path):
        logging.error("❌ Refusing to cache APK without verifiable Japanese resources: %s", path)
        return None
    digest = _sha256(path)
    suffix = path.suffix.lower()
    if suffix not in {".apk", ".apkm", ".apks", ".xapk", ".zip"}:
        suffix = ".apk"
    profile = delivery_profile(provider)
    asset_name = f"{_asset_prefix(package, version)}{digest}--dp_{profile}{suffix}"
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    target = CACHE_DIR / asset_name
    if not target.exists():
        temporary = target.with_name(f".{target.name}.part")
        shutil.copy2(path, temporary)
        temporary.replace(target)
    logging.info("📥 Staged verified Japanese APK cache candidate: %s %s (%s, profile=%s)", package, version, provider, profile)
    return target
