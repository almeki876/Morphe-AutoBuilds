import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from src.apk_language import (
    JapaneseResourceStatus,
    inspect_japanese_resources,
)


class ApkLanguageTests(unittest.TestCase):
    def _apk(self, root: Path, name: str = "app.apk", *entries: str) -> Path:
        path = root / name
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("AndroidManifest.xml", b"manifest")
            for entry in entries:
                archive.writestr(entry, b"resource")
        return path

    @mock.patch("src.apk_language._find_aapt", return_value=None)
    def test_missing_aapt_is_unverified(self, _aapt: mock.Mock) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = inspect_japanese_resources(self._apk(Path(directory)))
        self.assertIs(result.status, JapaneseResourceStatus.UNVERIFIED)
        self.assertIn("unavailable", result.detail)

    @mock.patch("src.apk_language._find_aapt", return_value="aapt")
    @mock.patch("src.apk_language.subprocess.run")
    def test_aapt_config_ja_is_present(
        self, run: mock.Mock, _aapt: mock.Mock
    ) -> None:
        run.return_value = subprocess.CompletedProcess(
            [], 0, "config (ja-rJP)\n", ""
        )
        with tempfile.TemporaryDirectory() as directory:
            result = inspect_japanese_resources(self._apk(Path(directory)))
        self.assertIs(result.status, JapaneseResourceStatus.PRESENT)

    @mock.patch("src.apk_language._find_aapt", return_value="aapt")
    @mock.patch("src.apk_language.subprocess.run")
    def test_successful_aapt_without_ja_is_absent(
        self, run: mock.Mock, _aapt: mock.Mock
    ) -> None:
        run.return_value = subprocess.CompletedProcess([], 0, "config en\n", "")
        with tempfile.TemporaryDirectory() as directory:
            result = inspect_japanese_resources(self._apk(Path(directory)))
        self.assertIs(result.status, JapaneseResourceStatus.ABSENT)

    @mock.patch("src.apk_language._find_aapt", return_value="aapt")
    @mock.patch("src.apk_language.subprocess.run")
    def test_aapt_failure_is_unverified(
        self, run: mock.Mock, _aapt: mock.Mock
    ) -> None:
        run.return_value = subprocess.CompletedProcess([], 1, "", "bad archive")
        with tempfile.TemporaryDirectory() as directory:
            result = inspect_japanese_resources(self._apk(Path(directory)))
        self.assertIs(result.status, JapaneseResourceStatus.UNVERIFIED)
        self.assertIn("exited with 1", result.detail)

    @mock.patch("src.apk_language._find_aapt", return_value=None)
    def test_values_bcp47_path_is_supplemental_present(
        self, _aapt: mock.Mock
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = inspect_japanese_resources(
                self._apk(Path(directory), "app.apk", "res/values-b+ja+JP/strings.xml")
            )
        self.assertIs(result.status, JapaneseResourceStatus.PRESENT)

    @mock.patch("src.apk_language._find_aapt", return_value=None)
    def test_locale_config_ja_is_supplemental_present(
        self, _aapt: mock.Mock
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = self._apk(root)
            with zipfile.ZipFile(path, "a") as archive:
                archive.writestr(
                    "res/xml/locales_config.xml",
                    '<locale-config><locale android:name="ja"/></locale-config>',
                )
            result = inspect_japanese_resources(path)
        self.assertIs(result.status, JapaneseResourceStatus.PRESENT)

    @mock.patch("src.apk_language._find_aapt", return_value=None)
    def test_split_container_uses_japanese_split_name(
        self, _aapt: mock.Mock
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base = self._apk(root, "base.apk")
            path = root / "bundle.apks"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("base.apk", base.read_bytes())
                archive.writestr("split_config.ja.apk", base.read_bytes())
            result = inspect_japanese_resources(path)
        self.assertIs(result.status, JapaneseResourceStatus.PRESENT)


if __name__ == "__main__":
    unittest.main()
