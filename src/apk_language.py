"""Validate that an APK or split container actually contains Japanese resources."""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path


class JapaneseResourceError(ValueError):
    """Raised when an inspected APK does not contain Japanese resources."""


class JapaneseResourceVerificationUnavailable(JapaneseResourceError):
    """Raised when Japanese resources cannot be inspected."""


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
    except OSError as error:
        raise JapaneseResourceVerificationUnavailable(
            f"Japanese resources could not be verified (aapt unavailable): {error}"
        ) from error

    if result.returncode != 0:
        diagnostics = (result.stderr or result.stdout or "unknown error").strip()
        raise JapaneseResourceVerificationUnavailable(
            "Japanese resources could not be verified "
            f"(aapt exited with {result.returncode}): {diagnostics[:300]}"
        )

    for line in result.stdout.splitlines():
        normalized = line.strip().lower().replace("_", "-")
        if re.search(r"\bconfig\s+\(?[^\s)]*\bja(?:-r?jp)?(?:\b|$)", normalized):
            return True
        if re.search(r"\blocale\s+\(?ja(?:-r?jp)?(?:\b|$)", normalized):
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
        raise JapaneseResourceVerificationUnavailable(
            "Japanese resources could not be verified (aapt/aapt2 unavailable)"
        )

    if path.suffix.casefold() == ".apk":
        if _resources_contain_japanese(path, aapt):
            return True
        raise JapaneseResourceError(f"APK contains no Japanese resource configuration: {path.name}")

    nested = _nested_apks(path)
    if not nested:
        raise JapaneseResourceError(f"split container contains no APKs: {path.name}")

    with tempfile.TemporaryDirectory(prefix="apk-ja-check-") as directory:
        root = Path(directory)
        for index, (_name, payload) in enumerate(nested):
            candidate = root / f"nested-{index}.apk"
            candidate.write_bytes(payload)
            if _resources_contain_japanese(candidate, aapt):
                return True

    raise JapaneseResourceError(
        f"split container contains no APK with Japanese resource configuration: {path.name}"
    )
