import tempfile
import unittest
import zipfile
from pathlib import Path

from src.apk_validation import ApkValidationError, validate_apk, validate_required_entries


class ApkValidationTests(unittest.TestCase):
    def _apk(self, entries: list[str]) -> Path:
        directory = Path(tempfile.mkdtemp())
        path = directory / "test.apk"
        with zipfile.ZipFile(path, "w") as archive:
            for entry in entries:
                archive.writestr(entry, b"content")
        return path

    def test_accepts_expected_native_abi(self) -> None:
        path = self._apk(["AndroidManifest.xml", "classes.dex", "lib/armeabi-v7a/libapp.so"])
        self.assertEqual(validate_apk(path, "armeabi-v7a"), {"armeabi-v7a"})

    def test_rejects_wrong_native_abi(self) -> None:
        path = self._apk(["AndroidManifest.xml", "classes.dex", "lib/armeabi-v7a/libapp.so"])
        with self.assertRaises(ApkValidationError):
            validate_apk(path, "arm64-v8a")

    def test_rejects_missing_dex(self) -> None:
        path = self._apk(["AndroidManifest.xml"])
        with self.assertRaises(ApkValidationError):
            validate_apk(path)

    def test_accepts_multiple_abis_for_universal(self) -> None:
        path = self._apk([
            "AndroidManifest.xml",
            "classes.dex",
            "lib/arm64-v8a/libapp.so",
            "lib/armeabi-v7a/libapp.so",
        ])
        self.assertEqual(
            validate_apk(path, "universal"),
            {"arm64-v8a", "armeabi-v7a"},
        )

    def test_accepts_single_abi_for_universal(self) -> None:
        path = self._apk([
            "AndroidManifest.xml",
            "classes.dex",
            "lib/arm64-v8a/libapp.so",
        ])
        self.assertEqual(validate_apk(path, "universal"), {"arm64-v8a"})

    def test_accepts_native_less_apk_for_universal(self) -> None:
        path = self._apk(["AndroidManifest.xml", "classes.dex"])
        self.assertEqual(validate_apk(path, "universal"), set())

    def test_accepts_native_less_apk_for_concrete_abi(self) -> None:
        path = self._apk(["AndroidManifest.xml", "classes.dex"])
        self.assertEqual(validate_apk(path, "arm64-v8a"), set())

    def test_required_patch_library_rejects_incomplete_apk(self) -> None:
        path = self._apk(["AndroidManifest.xml", "classes.dex"])
        with self.assertRaisesRegex(ApkValidationError, "libisvideoengine"):
            validate_required_entries(path, ("lib/*/libisvideoengine.so",))

    def test_required_patch_library_accepts_split_container(self) -> None:
        directory = Path(tempfile.mkdtemp())
        split = directory / "config.arm64_v8a.apk"
        with zipfile.ZipFile(split, "w") as archive:
            archive.writestr("lib/arm64-v8a/libisvideoengine.so", b"native")
        container = directory / "inshot.xapk"
        with zipfile.ZipFile(container, "w") as archive:
            archive.writestr("base.apk", self._apk(["AndroidManifest.xml", "classes.dex"]).read_bytes())
            archive.writestr(split.name, split.read_bytes())
        validate_required_entries(container, ("lib/*/libisvideoengine.so",))


if __name__ == "__main__":
    unittest.main()
