"""Deterministic archive helpers used by APK acquisition and VirusTotal scanning.

Android split containers (``.apks``, ``.apkm`` and ``.xapk``) are ZIP files.
Repacking identical APK payloads with filesystem timestamps makes the outer
SHA-256 change between runs, which defeats hash caches and VirusTotal lookups.
These helpers intentionally normalize only ZIP metadata; entry names and bytes
remain unchanged.
"""

from __future__ import annotations

import shutil
import zipfile
from pathlib import Path

ZIP_CONTAINER_SUFFIXES = frozenset({".apks", ".apkm", ".xapk", ".zip"})
_STABLE_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
_STABLE_MODE = 0o100644 << 16


def _stable_info(name: str, *, is_dir: bool = False) -> zipfile.ZipInfo:
    normalized = name.replace("\\", "/")
    if is_dir and not normalized.endswith("/"):
        normalized += "/"
    info = zipfile.ZipInfo(normalized, date_time=_STABLE_TIMESTAMP)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3
    info.external_attr = (0o40755 << 16) if is_dir else _STABLE_MODE
    info.flag_bits = 0
    info.extra = b""
    info.comment = b""
    return info


def write_files(target: Path, files: list[tuple[str, Path]]) -> Path:
    """Create a deterministic ZIP from named files.

    The same entry names and bytes always produce the same archive SHA-256,
    regardless of runner timestamps or source file metadata.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(
        target,
        "w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
        strict_timestamps=True,
    ) as archive:
        for name, source in sorted(files, key=lambda item: item[0]):
            archive.writestr(_stable_info(name), source.read_bytes(), compresslevel=9)
    return target


def canonicalize_zip(source: Path, target: Path) -> Path:
    """Rewrite a ZIP container with stable metadata while preserving payload bytes."""
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(source, "r") as original, zipfile.ZipFile(
        target,
        "w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
        strict_timestamps=True,
    ) as normalized:
        members = sorted(original.infolist(), key=lambda item: item.filename)
        for member in members:
            if member.is_dir():
                normalized.writestr(_stable_info(member.filename, is_dir=True), b"")
                continue
            normalized.writestr(
                _stable_info(member.filename),
                original.read(member),
                compresslevel=9,
            )
    return target


def copy_for_scan(source: Path, target: Path) -> Path:
    """Copy an APK input for scanning, normalizing ZIP wrappers when applicable."""
    if (
        source.suffix.casefold() in ZIP_CONTAINER_SUFFIXES
        and zipfile.is_zipfile(source)
    ):
        return canonicalize_zip(source, target)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    return target
