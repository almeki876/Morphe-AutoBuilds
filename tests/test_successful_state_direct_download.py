import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import save_successful_state


class SuccessfulStateDirectDownloadTests(unittest.TestCase):
    def test_refresh_catalog_uses_newest_published_release_and_skips_drafts(self) -> None:
        releases = [
            {
                "tag_name": "base-apk-cache-v2",
                "draft": True,
                "published_at": None,
                "created_at": "2026-08-24T06:30:00Z",
                "assets": [],
            },
            {
                "tag_name": "2026-08-24_15-12-JST",
                "draft": False,
                "published_at": "2026-08-24T06:12:00Z",
                "assets": [
                    {
                        "name": "youtube-arm64-v8a-morphe-v21.04.223.apk",
                        "browser_download_url": "https://example.invalid/new.apk",
                    }
                ],
            },
            {
                "tag_name": "2026-08-24_12-52-JST",
                "draft": False,
                "published_at": "2026-08-24T03:52:00Z",
                "assets": [
                    {
                        "name": "youtube-arm64-v8a-morphe-v21.04.223.apk",
                        "browser_download_url": "https://example.invalid/old.apk",
                    }
                ],
            },
        ]

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / "Morphe-AutoBuilds-Direct-Download.md"
            config = root / "my-patch-config.json"
            config.write_text(
                json.dumps(
                    {
                        "patch_list": [
                            {"app_name": "youtube", "source": "morphe"}
                        ]
                    }
                ),
                encoding="utf-8",
            )

            from scripts import generate_direct_download_md

            original_render = generate_direct_download_md.render

            def render_with_test_config(payload):
                return original_render(
                    payload,
                    source_root=root / "sources",
                    config_path=config,
                )

            with (
                mock.patch.object(save_successful_state, "_release_history", return_value=releases),
                mock.patch.object(generate_direct_download_md, "render", side_effect=render_with_test_config),
            ):
                save_successful_state._refresh_direct_download_catalog(output)

            text = output.read_text(encoding="utf-8")
            self.assertIn("2026-08-24 15:12 JST", text)
            self.assertIn("https://example.invalid/new.apk", text)
            self.assertNotIn("https://example.invalid/old.apk", text)
            self.assertNotIn("base-apk-cache-v2", text)


if __name__ == "__main__":
    unittest.main()
