"""Read and validate package/version identity from plain APK files."""

from __future__ import annotations

from dataclasses import dataclass
import os
import re
import shutil
import subprocess
from pathlib import Path

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
            directories.extend(
                sorted(
                    (path for path in build_tools.iterdir() if path.is_dir()),
                    reverse=True,
                )
            )
    return directories


def find_aapt() -> str | None:
    """Locate aapt/aapt2 using PATH or the Android SDK installed on Actions."""
    for name in ("aapt", "aapt2"):
        found = shutil.which(name)
        if found:
            return found

    executable_names = ("aapt.exe", "aapt2.exe") if os.name == "nt" else ("aapt", "aapt2")
    for directory in _build_tools_dirs():
        for name in executable_names:
            candidate = directory / name
            if candidate.is_file():
                return str(candidate)
    return None


def parse_badging(output: str) -> ApkIdentity:
    """Parse the package line emitted by ``aapt dump badging``."""
    match = _PACKAGE_RE.search(output)
    if not match:
        raise ApkIdentityError("aapt output did not contain APK package identity")
    code = match.group("code").strip() or None
    return ApkIdentity(
        package_name=match.group("package").strip(),
        version_name=match.group("name").strip(),
        version_code=code,
    )


def read_identity(path: Path) -> ApkIdentity:
    """Return package/version identity for a plain APK."""
    if path.suffix.casefold() != ".apk":
        raise ApkIdentityError(f"identity inspection requires a plain .apk: {path}")
    aapt = find_aapt()
    if not aapt:
        raise ApkIdentityError(
            "Android build-tools aapt/aapt2 was not found; cannot verify APK identity"
        )
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
    if result.returncode != 0:
        diagnostics = (result.stderr or result.stdout or "unknown error").strip()
        raise ApkIdentityError(
            f"aapt could not read APK identity for {path}: {diagnostics[:300]}"
        )
    return parse_badging(result.stdout)


def validate_identity(
    path: Path,
    expected_package: str,
    expected_candidate: VersionCandidate | None = None,
) -> ApkIdentity:
    """Verify package and optional release identity for one plain APK."""
    identity = read_identity(path)
    if identity.package_name != expected_package:
        raise ApkIdentityError(
            f"APK package mismatch: expected {expected_package}, "
            f"actual {identity.package_name}"
        )
    if expected_candidate and not expected_candidate.matches(
        identity.version_name,
        identity.version_code,
    ):
        raise ApkIdentityError(
            "APK version mismatch: expected "
            f"{expected_candidate.describe()}, actual "
            f"{identity.version_code or '?'} ({identity.version_name})"
        )
    return identity
