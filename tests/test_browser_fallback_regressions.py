from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from src import apk_validation, browser_fallback, downloader


class BrowserFallbackRegressionTests(unittest.TestCase):
    def test_uptodown_generic_current_download_is_not_historical_proof(self) -> None:
        self.assertTrue(
            browser_fallback._is_generic_uptodown_url(
                "https://example.en.uptodown.com/android/download"
            )
        )
        self.assertFalse(
            browser_fallback._is_generic_uptodown_url(
                "https://example.en.uptodown.com/android/download/123456789"
            )
        )
        self.assertFalse(
            browser_fallback._is_generic_uptodown_url(
                "https://dw.uptodown.com/dwn/release-token"
            )
        )

    def test_bundle_normalizer_rejects_unrelated_zip(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "not-an-apk.zip"
            with zipfile.ZipFile(archive, "w") as bundle:
                bundle.writestr("readme.txt", "not an APK bundle")

            with self.assertRaisesRegex(
                browser_fallback.BrowserFallbackError, "no APK modules"
            ):
                browser_fallback._normalize_apk_bundle(archive)

    def test_bundle_normalizer_merges_split_bundle_before_manifest_validation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / "release.xapk"
            with zipfile.ZipFile(archive, "w") as bundle:
                bundle.writestr("base.apk", b"base")
                bundle.writestr("config.arm64_v8a.apk", b"split")

            editor = root / "APKEditor.jar"
            editor.write_bytes(b"jar")
            validated: list[Path] = []
            commands: list[list[str]] = []

            def fake_validate(path: Path) -> None:
                validated.append(path)
                if path == archive:
                    raise apk_validation.ApkValidationError("root manifest missing")

            def fake_run(command, **kwargs):
                commands.append(command)
                output = archive.with_name("release-merged.apk")
                output.write_bytes(b"merged")
                return SimpleNamespace(returncode=0, stdout="merged")

            with mock.patch.object(downloader, "download_apkeditor", return_value=editor), \
                 mock.patch.object(apk_validation, "assert_valid_apk_archive", side_effect=fake_validate), \
                 mock.patch.object(browser_fallback.subprocess, "run", side_effect=fake_run):
                merged = browser_fallback._normalize_apk_bundle(archive)

            self.assertEqual(merged.name, "release-merged.apk")
            self.assertTrue(merged.exists())
            self.assertFalse(archive.exists())
            self.assertEqual(validated[-1], merged)
            self.assertTrue(commands)
            self.assertEqual(commands[0][:4], ["java", "-jar", str(editor), "m"])
            self.assertIn("-validate-modules", commands[0])


if __name__ == "__main__":
    unittest.main()
