"""Validate that an APK or split container actually contains Japanese resources."""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path


class JapaneseResourceError(ValueError):
    """Raised when an APK does not contain verifiable Japanese resources."""


def _find_aapt() -> str | None:
    for name in ("aapt2", "aapt"):
        found = shutil.which(name)
        if found:
            return found
    return None


def _resources_contain_japanese(path: Path, aapt: str) -> bool:
    try:
        result = subprocess.run(
            [aapt, "dump", "resources", str(path)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except OSError:
        return False

    # Android resource qualifiers are emitted as e.g. `config ja-rJP`.
    # Also accept a bare `config ja` form used by some aapt/aapt2 versions.
    for line in result.stdout.splitlines():
        normalized = line.strip().lower().replace("_", "-")
        if re.search(r"\bconfig\s+(?:[^\s]+-)?ja(?:-r?jp)?(?:\b|$)", normalized):
            return True
        if re.search(r"\blocale\s+ja(?:-r?jp)?(?:\b|$)", normalized):
            return True
    return False


def _nested_apks(path: Path) -> list[tuple[str, bytes]]:
    try:
        with zipfile.ZipFile(path) as archive:
            return [
                (name.replace("\\", "/"), archive.read(name))
                for name in archive.namelist()
                if name.casefold().endswith(".apk") and not name.endswith("/")
            ]
    except (OSError, zipfile.BadZipFile) as error:
        raise JapaneseResourceError(f"cannot inspect APK container {path}: {error}") from error


def contains_japanese(path: Path) -> bool:
    """Return true only when Japanese resources can be proven in the payload."""
    aapt = _find_aapt()
    if not aapt:
        raise JapaneseResourceError("aapt/aapt2 is unavailable; refusing unverified APK")

    if path.suffix.casefold() == ".apk":
        if _resources_contain_japanese(path, aapt):
            return True
        raise JapaneseResourceError(f"APK contains no Japanese resource configuration: {path.name}")

    nested = _nested_apks(path)
    if not nested:
        raise JapaneseResourceError(f"split container contains no APKs: {path.name}")

    with tempfile.TemporaryDirectory(prefix="apk-ja-check-") as directory:
        root = Path(directory)
        for index, (name, payload) in enumerate(nested):
            candidate = root / f"nested-{index}.apk"
            candidate.write_bytes(payload)
            # Filename evidence is useful for Play-style config.ja APKs, but
            # resource-table evidence is still required. This prevents an
            # English APK merely named config.ja.apk from passing validation.
            if _resources_contain_japanese(candidate, aapt):
                return True

    raise JapaneseResourceError(
        f"split container contains no APK with Japanese resource configuration: {path.name}"
    )
