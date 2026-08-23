import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from scripts import download_all_tools


class DownloadAllToolsTests(unittest.TestCase):
    def _run_main(self, patch_list: list[dict]) -> tuple[int, mock.Mock]:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            sources_dir = root / "sources"
            sources_dir.mkdir()
            config_path = root / "my-patch-config.json"
            config_path.write_text(
                json.dumps({"patch_list": patch_list}),
                encoding="utf-8",
            )
            download = mock.Mock(return_value=True)
            fake_src = types.ModuleType("src")
            fake_src.utils = types.ModuleType("src.utils")
            with (
                mock.patch.dict(
                    sys.modules,
                    {"src": fake_src, "src.utils": fake_src.utils},
                ),
                mock.patch.object(download_all_tools, "SOURCES_DIR", sources_dir),
                mock.patch.object(download_all_tools, "TOOLS_DIR", root / "tools"),
                mock.patch.object(
                    download_all_tools,
                    "PATCH_CONFIG_PATH",
                    config_path,
                ),
                mock.patch.object(download_all_tools, "download_asset_gh", download),
            ):
                result = download_all_tools.main()
            return result, download

    def test_unused_private_yuzu_bundle_is_not_a_global_dependency(self) -> None:
        result, download = self._run_main(
            [{"app_name": "youtube", "source": "morphe"}]
        )

        self.assertEqual(result, 0)
        download.assert_not_called()

    def test_yuzu_bundle_is_required_when_an_enabled_build_uses_it(self) -> None:
        result, download = self._run_main(
            [{"app_name": "example", "source": "yuzu"}]
        )

        self.assertEqual(result, 0)
        download.assert_called_once()

    def test_disabled_yuzu_build_does_not_require_private_credentials(self) -> None:
        result, download = self._run_main(
            [{"app_name": "example", "source": "yuzu", "enabled": False}]
        )

        self.assertEqual(result, 0)
        download.assert_not_called()

    def test_anddea_metadata_is_pinned_to_the_downloaded_bundle_tag(self) -> None:
        pinned_tag = "v4.3.0-dev.1"
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            sources_dir = root / "sources"
            sources_dir.mkdir()
            (sources_dir / "revanced-anddea.json").write_text(
                json.dumps(
                    [
                        {"name": "revanced-anddea"},
                        {
                            "user": "MorpheApp",
                            "repo": "morphe-cli",
                            "tag": "latest",
                        },
                        {
                            "user": "anddea",
                            "repo": "revanced-patches",
                            "tag": "latest",
                        },
                    ]
                ),
                encoding="utf-8",
            )
            config_path = root / "my-patch-config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "patch_list": [
                            {"app_name": "youtube", "source": "revanced-anddea"}
                        ]
                    }
                ),
                encoding="utf-8",
            )

            detect_release = mock.Mock(
                side_effect=[
                    {
                        "tag_name": "v1.2.3",
                        "assets": [
                            {
                                "name": "morphe-cli.jar",
                                "browser_download_url": "https://example.invalid/cli",
                            }
                        ],
                    },
                    {
                        "tag_name": pinned_tag,
                        "assets": [
                            {
                                "name": "revanced-patches.mpp",
                                "browser_download_url": "https://example.invalid/bundle",
                            }
                        ],
                    },
                ]
            )
            fake_src = types.ModuleType("src")
            fake_src.utils = types.ModuleType("src.utils")
            fake_src.utils.detect_github_release = detect_release
            download = mock.Mock(return_value=True)

            with (
                mock.patch.dict(
                    sys.modules,
                    {"src": fake_src, "src.utils": fake_src.utils},
                ),
                mock.patch.dict(
                    os.environ,
                    {"SOURCE_TAG_REVANCED_ANDDEA": pinned_tag},
                    clear=False,
                ),
                mock.patch.object(download_all_tools, "SOURCES_DIR", sources_dir),
                mock.patch.object(download_all_tools, "TOOLS_DIR", root / "tools"),
                mock.patch.object(
                    download_all_tools,
                    "PATCH_CONFIG_PATH",
                    config_path,
                ),
                mock.patch.object(download_all_tools, "download_asset", download),
            ):
                result = download_all_tools.main()

        self.assertEqual(result, 0)
        self.assertEqual(
            detect_release.call_args_list,
            [
                mock.call("MorpheApp", "morphe-cli", "latest"),
                mock.call("anddea", "revanced-patches", pinned_tag),
            ],
        )
        downloaded_urls = [call.args[0] for call in download.call_args_list]
        self.assertEqual(
            downloaded_urls[-1],
            "https://raw.githubusercontent.com/anddea/revanced-patches/"
            f"refs/tags/{pinned_tag}/patches-list.json",
        )
        self.assertNotIn("refs/heads/dev", downloaded_urls[-1])

    def test_tagged_metadata_url_requires_a_resolved_release(self) -> None:
        with self.assertRaisesRegex(ValueError, "resolved patch bundle release tag"):
            download_all_tools.tagged_patches_list_url(
                "anddea",
                "revanced-patches",
                " ",
                "patches-list.json",
            )


if __name__ == "__main__":
    unittest.main()
