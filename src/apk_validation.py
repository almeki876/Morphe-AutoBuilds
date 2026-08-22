"""Structural validation for APKs after split merging and patching."""

from __future__ import annotations

import re
import zipfile
from pathlib import Path


class ApkValidationError(ValueError):
    """Raised when an APK cannot be safely passed to or from the patcher."""


_ABI_PATH_RE = re.compile(r"^lib/([^/]+)/")


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
    return abis


def assert_valid_apk_archive(path: Path, expected_abi: str | None = None) -> None:
    """Assert that ``path`` is a structurally valid APK archive.

    This small assertion-style wrapper is used by download/normalization code
    that only needs success-or-exception semantics. Keeping it here ensures all
    callers share the same manifest/DEX/ZIP checks instead of maintaining a
    second, weaker archive validator.
    """
    validate_apk(path, expected_abi=expected_abi)
