"""Remove "split required" markers from a patched APK's binary manifest.

Some bundles (``.apkm``/``.xapk``/``.apks``) are produced by Android App
Bundle tooling, and their base module declares ``requiredSplitTypes`` (for
example ``base__abi,base__density``) on the ``<manifest>`` element. When the
CLI merges such a bundle into a single APK but leaves that attribute behind,
Android refuses to install the result: the package installer reports a missing
split, and Obtainium shows "failureIncompatible" ("the build does not match
this device").

A merged single APK is complete, so these markers are wrong and are removed.
Only ``requiredSplitTypes``, ``splitTypes`` and ``isSplitRequired`` on the root
``<manifest>`` element are touched; every other byte of the manifest stays.
"""

from __future__ import annotations

import os
import struct
import tempfile
import zipfile
from pathlib import Path

MANIFEST = "AndroidManifest.xml"
SPLIT_ATTRIBUTES = frozenset({"requiredSplitTypes", "splitTypes", "isSplitRequired"})

_RES_XML = 0x0003
_RES_STRING_POOL = 0x0001
_START_ELEMENT = 0x0102
_UTF8_FLAG = 0x100
_NO_INDEX = 0xFFFFFFFF
_ATTR_SIZE = 20
_ELEMENT_HEADER = 16
_ELEMENT_FIXED = 20


class ManifestSplitFixError(RuntimeError):
    """The manifest could not be parsed or rewritten safely."""


def _read_pool_string(data: bytes, pool_offset: int, index: int) -> str:
    (_, _, _, count, _, flags, strings_start, _) = struct.unpack_from(
        "<HHIIIIII", data, pool_offset
    )
    if index >= count:
        raise ManifestSplitFixError("string index outside the string pool")
    (rel,) = struct.unpack_from("<I", data, pool_offset + 28 + index * 4)
    pos = pool_offset + strings_start + rel
    if flags & _UTF8_FLAG:
        # Two length prefixes: UTF-16 character count, then UTF-8 byte count.
        first = data[pos]
        pos += 2 if first & 0x80 else 1
        second = data[pos]
        if second & 0x80:
            length = ((second & 0x7F) << 8) | data[pos + 1]
            pos += 2
        else:
            length = second
            pos += 1
        return data[pos : pos + length].decode("utf-8", "replace")
    first = struct.unpack_from("<H", data, pos)[0]
    pos += 2
    if first & 0x8000:
        first = ((first & 0x7FFF) << 16) | struct.unpack_from("<H", data, pos)[0]
        pos += 2
    return data[pos : pos + first * 2].decode("utf-16-le", "replace")


def strip_split_attributes(manifest: bytes) -> tuple[bytes, list[str]]:
    """Return ``(new_manifest, removed_attribute_names)``; unchanged if none."""
    if len(manifest) < 16:
        raise ManifestSplitFixError("manifest is too small to be binary XML")
    magic, _, total = struct.unpack_from("<HHI", manifest, 0)
    if magic != _RES_XML or total != len(manifest):
        raise ManifestSplitFixError("manifest is not well-formed binary XML")

    pool_offset: int | None = None
    offset = 8
    while offset + 8 <= len(manifest):
        chunk_type, _, chunk_size = struct.unpack_from("<HHI", manifest, offset)
        if chunk_size < 8 or offset + chunk_size > len(manifest):
            raise ManifestSplitFixError("corrupt chunk size in manifest")
        if chunk_type == _RES_STRING_POOL:
            pool_offset = offset
        elif chunk_type == _START_ELEMENT:
            if pool_offset is None:
                raise ManifestSplitFixError("string pool missing before first element")
            (name_index,) = struct.unpack_from("<I", manifest, offset + _ELEMENT_HEADER + 4)
            if _read_pool_string(manifest, pool_offset, name_index) != "manifest":
                return manifest, []
            return _rewrite_root(manifest, pool_offset, offset, chunk_size)
        offset += chunk_size
    return manifest, []


def _rewrite_root(
    manifest: bytes, pool_offset: int, offset: int, chunk_size: int
) -> tuple[bytes, list[str]]:
    base = offset + _ELEMENT_HEADER
    (ns, name, attr_start, attr_size, attr_count, id_idx, class_idx, style_idx) = struct.unpack_from(
        "<IIHHHHHH", manifest, base
    )
    if attr_size != _ATTR_SIZE or attr_start != _ELEMENT_FIXED:
        raise ManifestSplitFixError("unexpected attribute layout on <manifest>")
    attrs_at = base + attr_start
    if attrs_at + attr_count * _ATTR_SIZE > offset + chunk_size:
        raise ManifestSplitFixError("attributes overrun the <manifest> chunk")

    kept: list[bytes] = []
    removed: list[str] = []
    new_position: dict[int, int] = {}
    for i in range(attr_count):
        raw = manifest[attrs_at + i * _ATTR_SIZE : attrs_at + (i + 1) * _ATTR_SIZE]
        (attr_name,) = struct.unpack_from("<I", raw, 4)
        text = _read_pool_string(manifest, pool_offset, attr_name) if attr_name != _NO_INDEX else ""
        if text in SPLIT_ATTRIBUTES:
            removed.append(text)
        else:
            kept.append(raw)
            new_position[i + 1] = len(kept)  # id/class/style indexes are 1-based
    if not removed:
        return manifest, []

    def remap(index: int) -> int:
        return new_position.get(index, 0)

    element = struct.pack(
        "<IIHHHHHH", ns, name, attr_start, attr_size, len(kept),
        remap(id_idx), remap(class_idx), remap(style_idx),
    )
    header = bytearray(manifest[offset : offset + _ELEMENT_HEADER])
    new_chunk_size = _ELEMENT_HEADER + _ELEMENT_FIXED + len(kept) * _ATTR_SIZE
    struct.pack_into("<I", header, 4, new_chunk_size)
    new_chunk = bytes(header) + element + b"".join(kept)

    out = bytearray(manifest[:offset]) + new_chunk + manifest[offset + chunk_size :]
    struct.pack_into("<I", out, 4, len(out))
    return bytes(out), removed


def read_manifest(apk: Path) -> bytes:
    try:
        with zipfile.ZipFile(apk) as archive:
            return archive.read(MANIFEST)
    except (OSError, KeyError, zipfile.BadZipFile) as error:
        raise ManifestSplitFixError(f"cannot read {MANIFEST} from {apk}: {error}") from error


def required_split_attributes(apk: Path) -> list[str]:
    """Names of split-required markers present on the APK's ``<manifest>``."""
    return _find_split_attributes(read_manifest(apk))


def _find_split_attributes(manifest: bytes) -> list[str]:
    return strip_split_attributes(manifest)[1]


def strip_required_splits(apk: Path) -> list[str]:
    """Remove split-required markers in place. Returns the removed names.

    The APK is rewritten only when a marker is present, so ordinary builds pay
    nothing. Callers must run this BEFORE zip alignment and signing: both
    depend on the final bytes.
    """
    manifest = read_manifest(apk)
    fixed, removed = strip_split_attributes(manifest)
    if not removed:
        return []

    fd, tmp_name = tempfile.mkstemp(prefix=f".{apk.name}.", suffix=".tmp", dir=apk.parent)
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        with zipfile.ZipFile(apk) as src, zipfile.ZipFile(tmp, "w") as dst:
            for info in src.infolist():
                data = fixed if info.filename == MANIFEST else src.read(info.filename)
                clone = zipfile.ZipInfo(info.filename, date_time=info.date_time)
                clone.compress_type = info.compress_type
                clone.external_attr = info.external_attr
                clone.create_system = info.create_system
                dst.writestr(clone, data)
        tmp.replace(apk)
    except Exception as error:
        raise ManifestSplitFixError(f"could not rewrite {apk}: {error}") from error
    finally:
        tmp.unlink(missing_ok=True)
    return removed
