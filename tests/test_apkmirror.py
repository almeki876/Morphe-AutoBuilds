from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest.mock import patch

from src import apkmirror


class ApkMirrorTests(unittest.TestCase):
    def test_tiktok_prefers_standalone_apk_variant(self):
        config_path = (
            Path(__file__).resolve().parents[1]
            / "apps"
            / "apkmirror"
            / "tiktok.json"
        )
        config = json.loads(config_path.read_text(encoding="utf-8"))
        full_apk_score = apkmirror._variant_score(
            "47.4.3 APK arm64-v8a + armeabi-v7a Android 6.0+ nodpi",
            config,
            "arm64-v8a",
        )
        bundle_score = apkmirror._variant_score(
            "47.4.3 BUNDLE arm64-v8a + armeabi-v7a Android 6.0+ nodpi",
            config,
            "arm64-v8a",
        )

        self.assertEqual(config["type"], "APK")
        self.assertGreater(full_apk_score, bundle_score)

    def test_release_prefix_overrides_app_slug(self):
        config = {
            "org": "adobe",
            "name": "lightroom",
            "release_prefix": "lightroom-photo-video-editor",
        }

        self.assertEqual(
            apkmirror._configured_release_url("11.4.5", config),
            "https://www.apkmirror.com/apk/adobe/lightroom/"
            "lightroom-photo-video-editor-11-4-5-release/",
        )

    def test_release_prefix_defaults_to_app_slug(self):
        config = {"org": "adobe", "name": "lightroom"}

        self.assertEqual(
            apkmirror._configured_release_url("11.4.5", config),
            "https://www.apkmirror.com/apk/adobe/lightroom/"
            "lightroom-11-4-5-release/",
        )

    def test_explicit_release_prefix_bypasses_discovery_page(self):
        config = {
            "org": "adobe",
            "name": "lightroom",
            "release_prefix": "lightroom-photo-video-editor",
            "package": "com.adobe.lrmobile",
        }
        release_url = (
            "https://www.apkmirror.com/apk/adobe/lightroom/"
            "lightroom-photo-video-editor-11-4-5-release/"
        )

        with (
            patch("src.apkmirror._validated_release_url", return_value=release_url),
            patch("src.apkmirror._discovery_page") as discovery_page,
        ):
            self.assertEqual(
                apkmirror._discover_release("11.4.5", "lightroom", config),
                release_url,
            )

        discovery_page.assert_not_called()