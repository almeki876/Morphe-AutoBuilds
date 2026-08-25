import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from src import apk_cache, apk_validation
from src.apk_language import JapaneseResourceVerificationUnavailable


class CacheAndBrandingContractTests(unittest.TestCase):
    def test_cache_accepts_structurally_valid_apk_when_aapt_is_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            apk = Path(temp) / "base.apk"
            with zipfile.ZipFile(apk, "w") as archive:
                archive.writestr("AndroidManifest.xml", b"manifest")
            with patch(
                "src.apk_cache.contains_japanese",
                side_effect=JapaneseResourceVerificationUnavailable(
                    "Japanese resources could not be verified (aapt/aapt2 unavailable)"
                ),
            ):
                with self.assertLogs(level="WARNING") as logs:
                    self.assertTrue(apk_cache.is_valid_apk_archive(apk))
            self.assertIn("accepting unverified APK", "\n".join(logs.output))

    def test_provider_does_not_control_language_acceptance(self) -> None:
        self.assertNotEqual(
            apk_cache.delivery_profile("aurora-google-play"),
            apk_cache.delivery_profile("apkmirror"),
        )
        self.assertTrue(apk_cache.delivery_profile("apkmirror").endswith("generic-v1"))

    def test_legacy_profileless_asset_is_not_parseable(self) -> None:
        legacy = (
            "baseapk-v1--p_Y29tLmV4YW1wbGU--v_MS4w--"
            + "0" * 64
            + ".apks"
        )
        self.assertIsNone(apk_cache.parse_asset_name(legacy))

    def test_cache_stage_requires_japanese_payload_for_any_provider(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            apk = Path(temp) / "base.apk"
            with zipfile.ZipFile(apk, "w") as archive:
                archive.writestr("AndroidManifest.xml", b"manifest")
                archive.writestr("classes.dex", b"dex")

            with patch("src.apk_cache._contains_japanese", return_value=False):
                self.assertIsNone(
                    apk_cache.stage(apk, "com.example.app", "1.0", "apkmirror")
                )
                self.assertIsNone(
                    apk_cache.stage(apk, "com.example.app", "1.0", "aurora-google-play")
                )
                with patch.object(apk_cache, "CACHE_DIR", Path(temp) / "cache"):
                    staged_without_japanese = apk_cache.stage(
                        apk,
                        "com.example.app",
                        "1.0",
                        "apkmirror",
                        require_japanese=False,
                    )
                self.assertIsNotNone(staged_without_japanese)

            with patch("src.apk_cache._contains_japanese", return_value=True):
                with patch.dict(os.environ, {"BASE_APK_CACHE_DIR": temp}, clear=False):
                    staged = apk_cache.stage(
                        apk, "com.example.app", "1.0", "apkmirror"
                    )
                self.assertIsNotNone(staged)
                self.assertIn("apkmirror-generic-v1", staged.name)

    def test_anddea_icon_validator_rejects_default_icon_output(self) -> None:
        app = "youtube"
        source = "revanced-anddea"
        icon_root = Path("patch-assets/anddea/youtube/xisr_evergreen")
        foreground = next(icon_root.glob("mipmap-*/morphe_adaptive_foreground_custom.png"))
        background = next(icon_root.glob("mipmap-*/morphe_adaptive_background_custom.png"))

        with tempfile.TemporaryDirectory() as temp:
            apk = Path(temp) / "youtube-arm64-v8a-patch-v20.apk"
            with zipfile.ZipFile(apk, "w") as archive:
                archive.writestr("AndroidManifest.xml", b"manifest")
                archive.writestr("classes.dex", b"dex")
                archive.writestr("res/mipmap/ic_launcher.png", b"default")

            with patch.dict(os.environ, {"APP_NAME": app, "SOURCE": source}, clear=False):
                with self.assertRaises(apk_validation.ApkValidationError):
                    apk_validation.validate_apk(apk, expected_abi="universal")

            with zipfile.ZipFile(apk, "w") as archive:
                archive.writestr("AndroidManifest.xml", b"manifest")
                archive.writestr("classes.dex", b"dex")
                archive.writestr("res/mipmap/custom_foreground.png", foreground.read_bytes())
                archive.writestr("res/mipmap/custom_background.png", background.read_bytes())

            with patch.dict(os.environ, {"APP_NAME": app, "SOURCE": source}, clear=False):
                apk_validation.validate_apk(apk, expected_abi="universal")


if __name__ == "__main__":
    unittest.main()
