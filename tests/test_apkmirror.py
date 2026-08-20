from __future__ import annotations

import unittest
from unittest.mock import patch

from src import apkmirror


class ApkMirrorTests(unittest.TestCase):
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