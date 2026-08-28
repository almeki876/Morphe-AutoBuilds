import json
import tempfile
import unittest
from pathlib import Path

from scripts.generate_direct_download_md import parse_asset, render


class DirectDownloadMarkdownTests(unittest.TestCase):
    def _config(self, root: Path, targets: list[tuple[str, str]]) -> Path:
        path = root / "my-patch-config.json"
        path.write_text(
            json.dumps(
                {
                    "patch_list": [
                        {"app_name": app, "source": source}
                        for app, source in targets
                    ]
                }
            ),
            encoding="utf-8",
        )
        return path

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

    def test_render_is_minimal_and_uses_architecture_as_link_text(self):
        release = {
            "tag_name": "build-2026-08-24",
            "published_at": "2026-08-24T00:00:00Z",
            "assets": [
                {
                    "name": "youtube-universal-morphe-v21.04.223.apk",
                    "browser_download_url": "https://example.invalid/youtube-universal.apk",
                },
                {
                    "name": "youtube-arm64-v8a-morphe-v21.04.223.apk",
                    "browser_download_url": "https://example.invalid/youtube-arm64.apk",
                },
            ],
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = render(
                release,
                source_root=root,
                config_path=self._config(root, [("youtube", "morphe")]),
            )

        self.assertIn(
            "- Releases: [GitHub Releases](https://github.com/almeki876/Morphe-AutoBuilds/releases)",
            output,
        )
        self.assertIn("- 最終更新日時: 2026-08-24 09:00 JST", output)
        self.assertNotIn("参照Release", output)
        self.assertIn("## Morphe", output)
        self.assertIn("### YouTube", output)
        self.assertIn("[universal](https://example.invalid/youtube-universal.apk)", output)
        self.assertIn("[arm64-v8a](https://example.invalid/youtube-arm64.apk)", output)
        self.assertNotIn("⬇️", output)
        self.assertNotIn("Version:", output)
        self.assertNotIn("youtube-universal-morphe-v21.04.223.apk", output)
        self.assertNotIn("youtube-arm64-v8a-morphe-v21.04.223.apk", output)
        self.assertNotIn("ダウンロードしてください", output)

    def test_render_matches_obtainium_patch_config_order(self):
        release = {
            "assets": [
                {
                    "name": "google-photos-arm64-v8a-rookie-v7.40.0.apk",
                    "browser_download_url": "https://example.invalid/photos.apk",
                },
                {
                    "name": "youtube-arm64-v8a-revanced-anddea-v20.51.39.apk",
                    "browser_download_url": "https://example.invalid/anddea-youtube.apk",
                },
                {
                    "name": "youtube-music-arm64-v8a-morphe-v9.15.51.apk",
                    "browser_download_url": "https://example.invalid/morphe-music.apk",
                },
                {
                    "name": "youtube-arm64-v8a-morphe-v21.04.223.apk",
                    "browser_download_url": "https://example.invalid/morphe-youtube.apk",
                },
            ]
        }
        config_order = [
            ("youtube", "morphe"),
            ("youtube-music", "morphe"),
            ("youtube", "revanced-anddea"),
            ("google-photos", "rookie"),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = render(
                release,
                source_root=root,
                config_path=self._config(root, config_order),
            )

        morphe = output.index("## Morphe")
        morphe_youtube = output.index("### YouTube", morphe)
        morphe_music = output.index("### YouTube Music", morphe)
        anddea = output.index("## Anddea")
        rookie = output.index("## RookieEnough")
        self.assertLess(morphe, anddea)
        self.assertLess(anddea, rookie)
        self.assertLess(morphe_youtube, morphe_music)
        self.assertLess(morphe_music, anddea)

    def test_render_uses_gitlab_url_for_gitlab_patch_source(self):
        release = {
            "assets": [
                {
                    "name": "fing-arm64-v8a-paresh-v12.12.0.apk",
                    "browser_download_url": "https://example.invalid/fing.apk",
                }
            ]
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "paresh.json").write_text(
                json.dumps([
                    {"name": "paresh"},
                    {"user": "MorpheApp", "repo": "morphe-cli"},
                    {
                        "user": "Paresh-Maheshwari",
                        "repo": "paresh-patches",
                        "gitlab": True,
                    },
                ]),
                encoding="utf-8",
            )
            output = render(
                release,
                source_root=root,
                config_path=self._config(root, [("fing", "paresh")]),
            )

        self.assertIn(
            "## [Paresh-Maheshwari](https://gitlab.com/Paresh-Maheshwari/paresh-patches)",
            output,
        )

    def test_partial_release_preserves_unaffected_apps_and_replaces_updated_asset(self):
        old_youtube_url = "https://example.invalid/old/youtube.apk"
        old_photos_url = "https://example.invalid/old/photos.apk"
        new_youtube_url = "https://example.invalid/new/youtube.apk"
        releases = [
            {
                "tag_name": "new-partial",
                "published_at": "2026-08-24T02:00:00Z",
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
            root = Path(tmp)
            output = render(
                releases,
                source_root=root,
                config_path=self._config(
                    root,
                    [("youtube", "morphe"), ("google-photos", "rookie")],
                ),
            )

        self.assertIn(
            "- Releases: [GitHub Releases](https://github.com/almeki876/Morphe-AutoBuilds/releases)",
            output,
        )
        self.assertIn("- 最終更新日時: 2026-08-24 11:00 JST", output)
        self.assertNotIn("参照Release", output)
        self.assertIn(new_youtube_url, output)
        self.assertNotIn(old_youtube_url, output)
        self.assertIn(old_photos_url, output)

    def test_legacy_history_cannot_become_a_bogus_patch_source(self):
        bogus_url = "https://example.invalid/legacy/gboard.apk"
        valid_url = "https://example.invalid/current/gboard.apk"
        releases = [
            {
                "tag_name": "current",
                "published_at": "2026-08-24T02:00:00Z",
                "assets": [
                    {
                        "name": "gboard-arm64-v8a-adobo-v18.0.3.apk",
                        "browser_download_url": valid_url,
                    }
                ],
            },
            {
                "tag_name": "legacy",
                "published_at": "2026-08-23T02:00:00Z",
                "assets": [
                    {
                        "name": "gboard-arm64-v8a-adobo-v18.0.3.954559732-beta-arm64-v8a-v8a.apk",
                        "browser_download_url": bogus_url,
                    }
                ],
            },
        ]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = render(
                releases,
                source_root=root,
                config_path=self._config(root, [("gboard", "adobo")]),
            )

        self.assertIn(valid_url, output)
        self.assertNotIn(bogus_url, output)
        self.assertNotIn("## adobo-v18", output)

    def test_unmatched_apk_is_omitted_from_public_catalog(self):
        current_url = "https://example.invalid/current-legacy-name.apk"
        release = {
            "tag_name": "current",
            "published_at": "2026-08-24T02:00:00Z",
            "assets": [
                {
                    "name": "legacy-name.apk",
                    "browser_download_url": current_url,
                }
            ],
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = render(
                release,
                source_root=root,
                config_path=self._config(root, [("youtube", "morphe")]),
            )

        self.assertNotIn("Other APK assets", output)
        self.assertNotIn(current_url, output)
        self.assertNotIn("legacy-name.apk", output)
        self.assertIn("現在ダウンロードできるAPKはありません。", output)


if __name__ == "__main__":
    unittest.main()
