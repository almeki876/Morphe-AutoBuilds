import tempfile
import unittest
import zipfile
from pathlib import Path

from src.apk_validation import ApkValidationError, validate_apk


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


if __name__ == "__main__":
    unittest.main()