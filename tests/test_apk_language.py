import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src.apk_language import (
    JapaneseResourceError,
    JapaneseResourceVerificationUnavailable,
    contains_japanese,
)


class ApkLanguageTests(unittest.TestCase):
    @mock.patch("src.apk_language._find_aapt", return_value=None)
    def test_missing_aapt_is_reported_as_unverified(self, _aapt: mock.Mock) -> None:
        with self.assertRaisesRegex(
            JapaneseResourceVerificationUnavailable,
            "could not be verified.*unavailable",
        ):
            contains_japanese(Path("app.apk"))

    @mock.patch("src.apk_language._find_aapt", return_value="aapt")
    @mock.patch("src.apk_language._resources_contain_japanese", return_value=True)
    def test_plain_apk_with_japanese_resources_is_accepted(self, resources: mock.Mock, _aapt: mock.Mock) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "app.apk"
            path.write_bytes(b"apk")
            self.assertTrue(contains_japanese(path))
        resources.assert_called_once()

    @mock.patch("src.apk_language._find_aapt", return_value="aapt")
    @mock.patch("src.apk_language._resources_contain_japanese", return_value=False)
    def test_plain_english_apk_is_rejected(self, _resources: mock.Mock, _aapt: mock.Mock) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "app.apk"
            path.write_bytes(b"apk")
            with self.assertRaises(JapaneseResourceError):
                contains_japanese(path)

    @mock.patch("src.apk_language._find_aapt", return_value="aapt")
    @mock.patch("src.apk_language._resources_contain_japanese", side_effect=[False, True])
    def test_split_container_accepts_japanese_split_from_any_provider(self, _resources: mock.Mock, _aapt: mock.Mock) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bundle.apks"
            import zipfile
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("base.apk", b"base")
                archive.writestr("config.ja.apk", b"ja")
            self.assertTrue(contains_japanese(path))

    @mock.patch("src.apk_language._find_aapt", return_value="aapt")
    @mock.patch("src.apk_language._resources_contain_japanese", return_value=False)
    def test_split_container_without_japanese_split_is_rejected(self, _resources: mock.Mock, _aapt: mock.Mock) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bundle.apks"
            import zipfile
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("base.apk", b"base")
                archive.writestr("config.en.apk", b"en")
            with self.assertRaises(JapaneseResourceError):
                contains_japanese(path)


if __name__ == "__main__":
    unittest.main()
