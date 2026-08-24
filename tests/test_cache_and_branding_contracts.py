import hashlib
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from src import apk_cache, apk_validation


class CacheAndBrandingContractTests(unittest.TestCase):
    def test_google_play_profile_is_distinct_from_generic(self) -> None:
        self.assertEqual(
            apk_cache.delivery_profile("aurora-google-play"),
            apk_cache.GOOGLE_PLAY_JA_PROFILE,
        )
        self.assertEqual(apk_cache.delivery_profile("apkmirror"), apk_cache.GENERIC_PROFILE)
        self.assertNotEqual(
            apk_cache.delivery_profile("aurora-google-play"),
            apk_cache.delivery_profile("apkmirror"),
        )

    def test_legacy_profileless_asset_is_not_parseable(self) -> None:
        legacy = (
            "baseapk-v1--p_Y29tLmV4YW1wbGU--v_MS4w--"
            + "0" * 64
            + ".apks"
        )
        self.assertIsNone(apk_cache.parse_asset_name(legacy))

    def test_split_container_requires_japanese_language_split_for_play_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            good = root / "good.apks"
            bad = root / "bad.apks"
            for target, names in (
                (good, ["base.apk", "config.ja.apk", "config.arm64_v8a.apk"]),
                (bad, ["base.apk", "config.arm64_v8a.apk", "config.xxhdpi.apk"]),
            ):
                with zipfile.ZipFile(target, "w") as archive:
                    for name in names:
                        archive.writestr(name, b"placeholder")

            self.assertTrue(apk_cache._contains_japanese_language_split(good))
            self.assertFalse(apk_cache._contains_japanese_language_split(bad))

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
