"""Structural validation for APKs after split merging and patching."""

from __future__ import annotations

import hashlib
import json
import os
import re
import zipfile
from pathlib import Path


class ApkValidationError(ValueError):
    """Raised when an APK cannot be safely passed to or from the patcher."""


_ABI_PATH_RE = re.compile(r"^lib/([^/]+)/")
_BRANDED_SOURCES = {"revanced-anddea"}
_BRANDED_APPS = {
    "youtube": "patch-assets/anddea/youtube/xisr_evergreen",
    "youtube-music": "patch-assets/anddea/youtube-music/xisr_yellow",
}


def _is_patched_artifact(path: Path) -> bool:
    """Return True only for post-patch APKs, not the unmodified build input."""
    name = path.name.lower()
    return "-patch-v" in name or "-revanced-anddea-v" in name


def _validate_anddea_custom_icon(path: Path) -> None:
    """Prove that the configured Anddea icon assets survived patching/signing.

    The patch CLI can report ``Applied: Custom branding ...`` even when an
    option is ignored or an asset lookup falls back. Checking the actual output
    bytes closes that gap: both adaptive foreground and background PNGs from
    the vendored icon set must be present in the resulting APK.
    """
    app_name = os.getenv("APP_NAME", "").strip()
    source = os.getenv("SOURCE", "").strip()
    asset_root = _BRANDED_APPS.get(app_name)
    if source not in _BRANDED_SOURCES or not asset_root or not _is_patched_artifact(path):
        return

    root = Path(asset_root)
    if not root.is_dir():
        raise ApkValidationError(
            f"Anddea custom icon asset directory is missing: {root}"
        )

    expected: dict[str, set[str]] = {"foreground": set(), "background": set()}
    for density_dir in root.glob("mipmap-*"):
        if not density_dir.is_dir():
            continue
        for kind in expected:
            filename = f"morphe_adaptive_{kind}_custom.png"
            candidate = density_dir / filename
            if candidate.is_file():
                expected[kind].add(
                    hashlib.sha256(candidate.read_bytes()).hexdigest()
                )

    if not expected["foreground"] or not expected["background"]:
        raise ApkValidationError(
            f"Anddea custom icon asset set is incomplete for {app_name}: {root}"
        )

    actual_hashes: set[str] = set()
    try:
        with zipfile.ZipFile(path) as archive:
            for name in archive.namelist():
                if not name.lower().endswith(".png"):
                    continue
                try:
                    with archive.open(name) as handle:
                        digest = hashlib.sha256(handle.read()).hexdigest()
                except OSError:
                    continue
                actual_hashes.add(digest)
    except (OSError, zipfile.BadZipFile) as error:
        raise ApkValidationError(
            f"could not inspect patched APK icon resources: {path}"
        ) from error

    missing = [
        kind
        for kind, hashes in expected.items()
        if not hashes.intersection(actual_hashes)
    ]
    if missing:
        raise ApkValidationError(
            "Anddea custom icon assets are missing from the patched APK: "
            + ", ".join(missing)
            + f" (app={app_name}, source={source})"
        )


def validate_apk(path: Path, expected_abi: str | None = None) -> set[str]:
    """Validate core APK entries and return native ABIs present in the ZIP."""
    try:
        with zipfile.ZipFile(path) as archive:
            if archive.testzip() is not None:
                raise ApkValidationError(f"APK contains a corrupt ZIP entry: {path}")
            names = {
                name.replace("\\", "/").lstrip("/")
                for name in archive.namelist()
            }
    except (OSError, zipfile.BadZipFile) as error:
        raise ApkValidationError(f"APK is not a readable ZIP archive: {path}") from error

    if "AndroidManifest.xml" not in names:
        raise ApkValidationError(f"APK has no AndroidManifest.xml: {path}")
    if not any(
        name == "classes.dex" or re.fullmatch(r"classes\d+\.dex", name)
        for name in names
    ):
        raise ApkValidationError(f"APK has no DEX files: {path}")

    abis = {
        match.group(1)
        for name in names
        if (match := _ABI_PATH_RE.match(name))
    }
    if expected_abi and expected_abi != "universal" and abis and expected_abi not in abis:
        raise ApkValidationError(
            f"APK has no native libraries for requested ABI {expected_abi}; "
            f"found: {', '.join(sorted(abis))}"
        )

    _validate_anddea_custom_icon(path)
    return abis


def assert_valid_apk_archive(path: Path, expected_abi: str | None = None) -> None:
    """Assert that ``path`` is a structurally valid APK archive.

    This small assertion-style wrapper is used by download/normalization code
    that only needs success-or-exception semantics. Keeping it here ensures all
    callers share the same manifest/DEX/ZIP checks instead of maintaining a
    second, weaker archive validator.
    """
    validate_apk(path, expected_abi=expected_abi)
