from __future__ import annotations

import importlib.util
import struct
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

# src/__init__.py needs network libraries; load the standalone module directly.
_SPEC = importlib.util.spec_from_file_location(
    "manifest_split_fix", Path(__file__).resolve().parents[1] / "src" / "manifest_split_fix.py"
)
fix = importlib.util.module_from_spec(_SPEC)
sys.modules["manifest_split_fix"] = fix
_SPEC.loader.exec_module(fix)


def _pool(strings: list[str]) -> bytes:
    blobs, offsets, pos = [], [], 0
    for text in strings:
        raw = text.encode("utf-16-le")
        blob = struct.pack("<H", len(text)) + raw + b"\0\0"
        offsets.append(pos)
        blobs.append(blob)
        pos += len(blob)
    data = b"".join(blobs)
    data += b"\0" * (-len(data) % 4)
    strings_start = 28 + 4 * len(strings)
    size = strings_start + len(data)
    head = struct.pack("<HHIIIIII", 1, 28, size, len(strings), 0, 0, strings_start, 0)
    return head + b"".join(struct.pack("<I", o) for o in offsets) + data


def _attr(name_idx: int, raw: int = 0xFFFFFFFF, dtype: int = 0x10, data: int = 0) -> bytes:
    return struct.pack("<IIIHBBI", 0xFFFFFFFF, name_idx, raw, 8, 0, dtype, data)


def _binary_manifest(extra_attrs: list[str], id_attr: int = 0) -> bytes:
    names = ["manifest", "package", "versionCode"] + extra_attrs
    pool = _pool(names)
    attrs = [_attr(1), _attr(2, data=7)] + [_attr(3 + i) for i in range(len(extra_attrs))]
    element = struct.pack(
        "<IIHHHHHH", 0xFFFFFFFF, 0, 20, 20, len(attrs), id_attr, 0, 0
    )
    start_body = element + b"".join(attrs)
    start = struct.pack("<HHIII", 0x0102, 16, 16 + len(start_body), 1, 0xFFFFFFFF) + start_body
    end_body = struct.pack("<II", 0xFFFFFFFF, 0)
    end = struct.pack("<HHIII", 0x0103, 16, 16 + len(end_body), 1, 0xFFFFFFFF) + end_body
    body = pool + start + end
    return struct.pack("<HHI", 3, 8, 8 + len(body)) + body


def _make_apk(path: Path, manifest: bytes) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("AndroidManifest.xml", manifest)
        archive.writestr("classes.dex", b"dex\n035\0" + b"x" * 64, zipfile.ZIP_DEFLATED)
        archive.writestr("resources.arsc", b"arsc" * 8)


class StripSplitAttributesTests(unittest.TestCase):
    def test_removes_split_markers_and_keeps_other_attributes(self) -> None:
        original = _binary_manifest(["requiredSplitTypes", "splitTypes", "isSplitRequired"])
        fixed, removed = fix.strip_split_attributes(original)
        self.assertEqual(removed, ["requiredSplitTypes", "splitTypes", "isSplitRequired"])
        self.assertEqual(len(original) - len(fixed), 3 * 20)
        self.assertEqual(struct.unpack_from("<I", fixed, 4)[0], len(fixed))
        again, removed_again = fix.strip_split_attributes(fixed)
        self.assertEqual((again, removed_again), (fixed, []))

    def test_manifest_without_markers_is_unchanged(self) -> None:
        original = _binary_manifest(["extra"])
        self.assertEqual(fix.strip_split_attributes(original), (original, []))

    def test_rejects_non_manifest_data(self) -> None:
        with self.assertRaises(fix.ManifestSplitFixError):
            fix.strip_split_attributes(b"not xml at all, definitely")


class StripRequiredSplitsTests(unittest.TestCase):
    def test_apk_is_rewritten_only_when_needed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            broken = Path(tmp) / "broken.apk"
            _make_apk(broken, _binary_manifest(["requiredSplitTypes", "splitTypes"]))
            self.assertEqual(
                fix.required_split_attributes(broken), ["requiredSplitTypes", "splitTypes"]
            )
            self.assertEqual(fix.strip_required_splits(broken), ["requiredSplitTypes", "splitTypes"])
            self.assertEqual(fix.required_split_attributes(broken), [])
            with zipfile.ZipFile(broken) as archive:
                self.assertIsNone(archive.testzip())
                self.assertEqual(
                    archive.namelist(), ["AndroidManifest.xml", "classes.dex", "resources.arsc"]
                )
                self.assertEqual(archive.read("resources.arsc"), b"arsc" * 8)

            clean = Path(tmp) / "clean.apk"
            _make_apk(clean, _binary_manifest(["extra"]))
            before = clean.read_bytes()
            self.assertEqual(fix.strip_required_splits(clean), [])
            self.assertEqual(clean.read_bytes(), before)


class PipelineWiringTests(unittest.TestCase):
    def test_build_strips_before_alignment_and_checks_after_signing(self) -> None:
        text = (Path(__file__).resolve().parents[1] / "src" / "__main__.py").read_text(encoding="utf-8")
        strip = text.index("manifest_split_fix.strip_required_splits(output_apk)")
        align = text.index("_align_apk(output_apk)")
        sign = text.index("_sign_apk(output_apk, signed_apk, app_name)")
        check = text.index("manifest_split_fix.required_split_attributes(signed_apk)")
        self.assertLess(strip, align)
        self.assertLess(align, sign)
        self.assertLess(sign, check)


if __name__ == "__main__":
    unittest.main()
