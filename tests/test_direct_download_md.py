import tempfile
import unittest
from pathlib import Path

from scripts.generate_direct_download_md import parse_asset, render


class DirectDownloadMarkdownTests(unittest.TestCase):
    def test_parse_asset_extracts_app_arch_source_and_version(self):
        asset = {
            "name": "youtube-arm64-v8a-morphe-v21.04.223.apk",
            "browser_download_url": "https://example.invalid/youtube.apk",
        }
        parsed = parse_asset(asset)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.app, "youtube")
        self.assertEqual(parsed.arch, "arm64-v8a")
        self.assertEqual(parsed.source, "morphe")
        self.assertEqual(parsed.version, "21.04.223")

    def test_render_uses_release_asset_direct_urls(self):
        release = {
            "tag_name": "build-2026-08-24",
            "published_at": "2026-08-24T00:00:00Z",
            "html_url": "https://github.com/example/repo/releases/tag/build-2026-08-24",
            "assets": [
                {
                    "name": "youtube-universal-morphe-v21.04.223.apk",
                    "browser_download_url": "https://github.com/example/repo/releases/download/build-2026-08-24/youtube-universal-morphe-v21.04.223.apk",
                },
                {
                    "name": "youtube-arm64-v8a-morphe-v21.04.223.apk",
                    "browser_download_url": "https://github.com/example/repo/releases/download/build-2026-08-24/youtube-arm64-v8a-morphe-v21.04.223.apk",
                },
            ],
        }
        with tempfile.TemporaryDirectory() as tmp:
            output = render(release, source_root=Path(tmp))
        self.assertIn("# Direct APK Download Links", output)
        self.assertIn("### YouTube (Morphe)", output)
        self.assertIn("universal", output)
        self.assertIn("arm64-v8a", output)
        self.assertIn(release["assets"][0]["browser_download_url"], output)
        self.assertIn("掲載APK数: 2", output)

    def test_partial_release_preserves_unaffected_apps_and_replaces_updated_asset(self):
        old_youtube_url = "https://example.invalid/old/youtube.apk"
        old_photos_url = "https://example.invalid/old/photos.apk"
        new_youtube_url = "https://example.invalid/new/youtube.apk"
        releases = [
            {
                "tag_name": "new-partial",
                "published_at": "2026-08-24T02:00:00Z",
                "html_url": "https://github.com/example/repo/releases/tag/new-partial",
                "assets": [
                    {
                        "name": "youtube-arm64-v8a-morphe-v21.05.001.apk",
                        "browser_download_url": new_youtube_url,
                    }
                ],
            },
            {
                "tag_name": "old-full",
                "published_at": "2026-08-24T01:00:00Z",
                "html_url": "https://github.com/example/repo/releases/tag/old-full",
                "assets": [
                    {
                        "name": "youtube-arm64-v8a-morphe-v21.04.223.apk",
                        "browser_download_url": old_youtube_url,
                    },
                    {
                        "name": "google-photos-arm64-v8a-rookie-v7.40.0.apk",
                        "browser_download_url": old_photos_url,
                    },
                ],
            },
        ]
        with tempfile.TemporaryDirectory() as tmp:
            output = render(releases, source_root=Path(tmp))

        self.assertIn(new_youtube_url, output)
        self.assertNotIn(old_youtube_url, output)
        self.assertIn(old_photos_url, output)
        self.assertIn("部分リリースで更新対象にならなかったアプリ", output)
        self.assertIn("掲載APK数: 2", output)

    def test_unmatched_apk_is_still_listed(self):
        release = {
            "assets": [
                {
                    "name": "legacy-name.apk",
                    "browser_download_url": "https://example.invalid/legacy-name.apk",
                }
            ]
        }
        with tempfile.TemporaryDirectory() as tmp:
            output = render(release, source_root=Path(tmp))
        self.assertIn("## Other APK assets", output)
        self.assertIn("legacy-name.apk", output)
        self.assertIn("https://example.invalid/legacy-name.apk", output)


if __name__ == "__main__":
    unittest.main()
