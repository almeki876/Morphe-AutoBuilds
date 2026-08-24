"""Durable, integrity-checked cache for original APK inputs.

The cache is stored as assets on a draft GitHub Release. Draft releases are
visible to the workflow token but are not exposed on the repository's public
Releases page. A successfully used original is staged during the build and a
single workflow job uploads it after the matrix finishes.

Cache correctness is part of the APK input contract: Google Play delivery is
configuration-dependent, so package/version alone is not a safe cache key.
Language, density, ABI, device profile, and split-selection can change the
payload while the package/version remain identical. The cache therefore records
the delivery profile in the asset name and rejects the legacy profile-less
format instead of silently reusing an incompatible payload.
"""

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

CACHE_TAG = os.getenv("BASE_APK_CACHE_TAG", "base-apk-cache-v3")
CACHE_DIR = Path(os.getenv("BASE_APK_CACHE_DIR", "base-apk-cache-out"))
_ASSET_RE = re.compile(
    r"^baseapk-v2--p_([A-Za-z0-9_-]+)--v_([A-Za-z0-9_-]+)"
    r"--([0-9a-f]{64})--dp_([A-Za-z0-9_-]+)(\.(?:apk|apkm|apks|xapk|zip))$",
    re.IGNORECASE,
)
_REMOTE_MISSES: set[tuple[str, str]] = set()

GOOGLE_PLAY_JA_PROFILE = "gplay-ja-jp-px9a-split-v1"
GENERIC_PROFILE = "generic-v1"


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


def delivery_profile(provider: str) -> str:
    """Return the immutable delivery contract represented by a cache asset."""
    if provider in {"aurora-google-play", "google-play"}:
        return GOOGLE_PLAY_JA_PROFILE
    return GENERIC_PROFILE


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
        if not path.is_file() or path.stat().st_size <= 0:
            return False
        if not zipfile.is_zipfile(path):
            logging.warning("APK cache rejected a non-ZIP input: %s", path)
            return False
        with zipfile.ZipFile(path) as archive:
            names = {
                name.replace("\\", "/").lstrip("/")
                for name in archive.namelist()
            }
        is_apk = "AndroidManifest.xml" in names
        is_split_container = any(name.casefold().endswith(".apk") for name in names)
        if not is_apk and not is_split_container:
            logging.warning(
                "APK cache rejected ZIP without AndroidManifest.xml or nested APKs: %s",
                path,
            )
            return False
        if expected_sha256 and _sha256(path) != expected_sha256:
            logging.warning("APK cache SHA-256 mismatch: %s", path)
            return False
        return True
    except OSError as error:
        logging.warning("APK cache validation failed for %s: %s", path, error)
        return False


def _contains_japanese_language_split(path: Path) -> bool:
    """Require an explicit Japanese Play language split when the payload is split.

    Play's split delivery names language configuration APKs with the locale in
    the filename (for example ``config.ja.apk``). This check deliberately
    fails closed for a split container with no Japanese split: otherwise a
    package/version cache hit can silently freeze an English-only payload.
    """
    if path.suffix.lower() not in {".apks", ".xapk", ".zip"}:
        return True
    try:
        with zipfile.ZipFile(path) as archive:
            nested = [
                name.replace("\\", "/").lstrip("/")
                for name in archive.namelist()
                if name.casefold().endswith(".apk")
            ]
    except (OSError, zipfile.BadZipFile) as error:
        logging.warning("Could not inspect split container for Japanese resources: %s", error)
        return False

    japanese = re.compile(r"(?:^|[._-])ja(?:[._-]|$)", re.IGNORECASE)
    found = any(japanese.search(Path(name).stem) for name in nested)
    if not found:
        logging.error(
            "❌ Google Play cache candidate has split APKs but no Japanese language split: %s",
            [Path(name).name for name in nested],
        )
    return found


def validate_asset(path: Path) -> bool:
    parsed = parse_asset_name(path.name)
    if not parsed:
        return False
    return _validate(path, parsed[2])


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


def _download_with_gh(repository: str, tag: str, prefix: str) -> list[Path]:
    """Download all matching draft-release assets through the GitHub CLI."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    pattern = f"{prefix}*"
    environment = os.environ.copy()
    token = environment.get("GITHUB_TOKEN") or environment.get("GH_TOKEN")
    if token:
        environment["GH_TOKEN"] = token
    try:
        result = subprocess.run(
            [
                "gh",
                "release",
                "download",
                tag,
                "--repo",
                repository,
                "--pattern",
                pattern,
                "--clobber",
                "--dir",
                str(CACHE_DIR),
            ],
            capture_output=True,
            text=True,
            env=environment,
            check=False,
        )
    except OSError as error:
        logging.warning("gh release download could not start: %s", error)
        return []
    if result.stdout.strip():
        logging.info("gh release download output: %s", result.stdout.strip())
    if result.returncode:
        logging.warning(
            "gh release download failed (exit %d): %s",
            result.returncode,
            result.stderr.strip() or "no stderr output",
        )
        return []
    if result.stderr.strip():
        logging.info("gh release download diagnostics: %s", result.stderr.strip())
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


def _candidate_sort_key(path: Path) -> tuple[int, int]:
    profile = _asset_profile(path.name)
    # Japanese Google Play delivery is the preferred cache variant. Generic
    # provider assets remain usable when no Play variant exists.
    rank = 0 if profile == GOOGLE_PLAY_JA_PROFILE else 1
    try:
        mtime = path.stat().st_mtime_ns
    except OSError:
        mtime = 0
    return rank, -mtime


def _usable_candidate(path: Path) -> bool:
    parsed = parse_asset_name(path.name)
    if not parsed or not validate_asset(path):
        return False
    profile = _asset_profile(path.name)
    if profile == GOOGLE_PLAY_JA_PROFILE:
        return _contains_japanese_language_split(path)
    if profile == GENERIC_PROFILE:
        return True
    return False


def restore(package: str, version: str, app_name: str) -> Path | None:
    """Restore an exact package/version without reusing legacy delivery variants."""
    if not _enabled() or not package or not version:
        return None

    prefix = _asset_prefix(package, version)
    if CACHE_DIR.exists():
        candidates = sorted(
            CACHE_DIR.glob(f"{prefix}*"),
            key=_candidate_sort_key,
        )
        for candidate in candidates:
            if _usable_candidate(candidate):
                restored = _copy_for_build(candidate, app_name, version)
                logging.info(
                    "📦 APK cache hit (local): %s %s profile=%s",
                    package,
                    version,
                    _asset_profile(candidate.name),
                )
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
        release = find_release(
            Github(auth=Auth.Token(token)).get_repo(repository)
        )
        if release is None:
            _REMOTE_MISSES.add(key)
            logging.info("APK cache release does not exist yet; using providers")
            return None

        matches = [
            asset for asset in release.get_assets() if asset.name.startswith(prefix)
        ]
        matches.sort(
            key=lambda asset: (
                0 if _asset_profile(asset.name) == GOOGLE_PLAY_JA_PROFILE else 1,
                -(asset.created_at or asset.updated_at).timestamp()
                if asset.created_at or asset.updated_at
                else 0,
            )
        )
        downloaded = _download_with_gh(repository, release.tag_name, prefix)
        for candidate in sorted(downloaded, key=_candidate_sort_key):
            if _usable_candidate(candidate):
                restored = _copy_for_build(candidate, app_name, version)
                logging.info(
                    "📦 APK cache hit (GitHub CLI): %s %s profile=%s",
                    package,
                    version,
                    _asset_profile(candidate.name),
                )
                return restored
        for asset in matches:
            if not _asset_profile(asset.name):
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
                if not _usable_candidate(candidate):
                    candidate.unlink(missing_ok=True)
                    continue
                restored = _copy_for_build(candidate, app_name, version)
                candidate.unlink(missing_ok=True)
                logging.info(
                    "📦 APK cache hit (GitHub): %s %s profile=%s",
                    package,
                    version,
                    _asset_profile(asset.name),
                )
                return restored
            except Exception as error:
                logging.warning("APK cache asset download failed: %s", error)

    except GithubException as error:
        logging.warning("APK cache lookup failed; using providers: %s", error)
    except Exception as error:
        logging.warning(
            "APK cache lookup failed unexpectedly; using providers: %s",
            error,
        )

    _REMOTE_MISSES.add(key)
    logging.info("APK cache miss: %s %s", package, version)
    return None


def stage(path: Path, package: str, version: str, provider: str) -> Path | None:
    """Stage a provider download for upload after a successful build."""
    profile = delivery_profile(provider)
    if not _enabled() or not package or not version or not _validate(path):
        return None
    if profile == GOOGLE_PLAY_JA_PROFILE and not _contains_japanese_language_split(path):
        logging.error(
            "❌ Refusing to cache Google Play APK without an explicit Japanese language split: %s",
            path,
        )
        return None

    digest = _sha256(path)
    suffix = path.suffix.lower()
    if suffix not in {".apk", ".apkm", ".apks", ".xapk", ".zip"}:
        suffix = ".apk"
    asset_name = f"{_asset_prefix(package, version)}{digest}--dp_{profile}{suffix}"
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    target = CACHE_DIR / asset_name
    if not target.exists():
        temporary = target.with_name(f".{target.name}.part")
        shutil.copy2(path, temporary)
        temporary.replace(target)
    logging.info(
        "📥 Staged verified APK cache candidate: %s %s (%s, profile=%s)",
        package,
        version,
        provider,
        profile,
    )
    return target
