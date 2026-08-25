"""Read and validate package/version identity from APK inputs."""

from __future__ import annotations

from dataclasses import dataclass
import logging
import os
import re
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

from src.apk_language import (
    JapaneseResourceError,
    JapaneseResourceVerificationUnavailable,
    contains_japanese,
)
from src.versioning import VersionCandidate


_PACKAGE_RE = re.compile(
    r"^package:\s+name='(?P<package>[^']+)'"
    r"\s+versionCode='(?P<code>[^']*)'"
    r"\s+versionName='(?P<name>[^']*)'",
    re.MULTILINE,
)


class ApkIdentityError(ValueError):
    """Raised when an APK identity cannot be read or does not match expectations."""


@dataclass(frozen=True)
class ApkIdentity:
    package_name: str
    version_name: str
    version_code: str | None


def _build_tools_dirs() -> list[Path]:
    roots: list[Path] = []
    for env_name in ("ANDROID_HOME", "ANDROID_SDK_ROOT"):
        value = os.getenv(env_name)
        if value:
            roots.append(Path(value))
    roots.append(Path("/usr/local/lib/android/sdk"))

    directories: list[Path] = []
    for root in roots:
        build_tools = root / "build-tools"
        if build_tools.is_dir():
            directories.extend(sorted((path for path in build_tools.iterdir() if path.is_dir()), reverse=True))
    return directories


def find_aapt() -> str | None:
    """Locate aapt/aapt2 using PATH or the Android SDK installed on Actions."""
    for name in ("aapt", "aapt2"):
        found = shutil.which(name)
        if found:
            return found
    executable_names = (("aapt.exe", "aapt2.exe") if os.name == "nt" else ("aapt", "aapt2"))
    for directory in _build_tools_dirs():
        for name in executable_names:
            candidate = directory / name
            if candidate.is_file():
                return str(candidate)
    return None


def parse_badging(output: str) -> ApkIdentity:
    match = _PACKAGE_RE.search(output)
    if not match:
        raise ApkIdentityError("aapt output did not contain APK package identity")
    code = match.group("code").strip() or None
    return ApkIdentity(
        package_name=match.group("package").strip(),
        version_name=match.group("name").strip(),
        version_code=code,
    )


def _read_plain_apk_identity(path: Path, aapt: str) -> ApkIdentity:
    try:
        result = subprocess.run(
            [aapt, "dump", "badging", str(path)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except OSError as error:
        raise ApkIdentityError(f"could not start aapt for {path}: {error}") from error
    try:
        identity = parse_badging(result.stdout)
    except ApkIdentityError:
        if result.returncode != 0:
            diagnostics = (result.stderr or result.stdout or "unknown error").strip()
            raise ApkIdentityError(f"aapt could not read APK identity for {path}: {diagnostics[:300]}")
        raise
    return identity


def _nested_apk_priority(name: str) -> tuple[int, str]:
    normalized = name.replace("\\", "/").casefold()
    basename = normalized.rsplit("/", 1)[-1]
    if basename == "base.apk":
        return (0, normalized)
    if "base-master" in basename or basename.startswith("base-"):
        return (1, normalized)
    return (2, normalized)


def read_identity(path: Path) -> ApkIdentity:
    """Return package/version identity for a plain APK or split container."""
    aapt = find_aapt()
    if not aapt:
        raise ApkIdentityError("Android build-tools aapt/aapt2 was not found; cannot verify APK identity")

    if path.suffix.casefold() == ".apk":
        return _read_plain_apk_identity(path, aapt)

    try:
        with zipfile.ZipFile(path) as archive:
            nested = sorted(
                (name for name in archive.namelist() if name.casefold().endswith(".apk") and not name.endswith("/")),
                key=_nested_apk_priority,
            )
            if not nested:
                raise ApkIdentityError(f"split container contains no nested APK files: {path}")
            errors: list[str] = []
            identities: list[tuple[int, ApkIdentity]] = []
            with tempfile.TemporaryDirectory(prefix="apk-identity-") as directory:
                for index, name in enumerate(nested):
                    extracted = Path(directory) / f"candidate-{index}.apk"
                    with archive.open(name) as source, extracted.open("wb") as target:
                        shutil.copyfileobj(source, target)
                    try:
                        identity = _read_plain_apk_identity(extracted, aapt)
                    except ApkIdentityError as error:
                        errors.append(f"{name}: {error}")
                        continue
                    identities.append((index, identity))
            if identities:
                _, best = min(identities, key=lambda item: (0 if item[1].version_name else 1, item[0]))
                return best
    except zipfile.BadZipFile as error:
        raise ApkIdentityError(f"APK input is not a readable ZIP archive: {path}") from error

    raise ApkIdentityError(f"could not read identity from nested APKs in {path}: {'; '.join(errors[:3])}")


def validate_identity(
    path: Path,
    expected_package: str,
    expected_candidate: VersionCandidate | None = None,
    *,
    require_japanese: bool = True,
) -> ApkIdentity:
    """Verify package/version and inspect Japanese resources in the payload."""
    identity = read_identity(path)
    if identity.package_name != expected_package:
        raise ApkIdentityError(f"APK package mismatch: expected {expected_package}, actual {identity.package_name}")
    if expected_candidate and not expected_candidate.matches(identity.version_name, identity.version_code):
        raise ApkIdentityError(
            "APK version mismatch: expected "
            f"{expected_candidate.describe()}, actual {identity.version_code or '?'} ({identity.version_name})"
        )
    try:
        if not contains_japanese(path):
            raise JapaneseResourceError("Japanese resources were not detected")
    except JapaneseResourceVerificationUnavailable as error:
        logging.warning("⚠️  %s; accepting unverified APK: %s", error, path)
    except JapaneseResourceError as error:
        if require_japanese:
            raise ApkIdentityError(f"APK does not contain Japanese resources: {error}") from error
        logging.warning("⚠️  APK accepted although Japanese resources were not detected: %s", path)
    return identity
