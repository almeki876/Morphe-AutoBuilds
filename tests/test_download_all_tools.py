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
                # CLI: GitHub "releases/latest" (stable only).
                mock.call("MorpheApp", "morphe-cli", "latest", include_prereleases=False),
                # Patch bundles follow the newest release, pre-releases included.
                mock.call("anddea", "revanced-patches", pinned_tag, include_prereleases=True),
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

    def test_excluded_sources_are_skipped_without_download(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            sources_dir = root / "sources"
            sources_dir.mkdir()
            (sources_dir / "excluded.json").write_text(
                json.dumps([{"name": "excluded"}]), encoding="utf-8"
            )
            fake_src = types.ModuleType("src")
            fake_src.utils = types.ModuleType("src.utils")
            fake_src.utils.detect_github_release = mock.Mock()

            with (
                mock.patch.dict(
                    sys.modules,
                    {"src": fake_src, "src.utils": fake_src.utils},
                ),
                mock.patch.dict(
                    os.environ,
                    {"SOURCE_TAGS_JSON": json.dumps({"other": "v1.0.0"})},
                    clear=False,
                ),
                mock.patch.object(download_all_tools, "SOURCES_DIR", sources_dir),
                mock.patch.object(download_all_tools, "TOOLS_DIR", root / "tools"),
            ):
                result = download_all_tools.main()

            self.assertEqual(result, 0)
            fake_src.utils.detect_github_release.assert_not_called()

    def test_partial_download_failure_continues_and_succeeds(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            sources_dir = root / "sources"
            sources_dir.mkdir()
            (sources_dir / "good.json").write_text(
                json.dumps([
                    {"name": "good"},
                    {"user": "MorpheApp", "repo": "morphe-cli", "tag": "latest"},
                    {"user": "good-user", "repo": "good-patches", "tag": "latest"},
                ]),
                encoding="utf-8",
            )
            (sources_dir / "bad.json").write_text(
                json.dumps([
                    {"name": "bad"},
                    {"user": "MorpheApp", "repo": "morphe-cli", "tag": "latest"},
                    {"user": "bad-user", "repo": "bad-patches", "tag": "latest"},
                ]),
                encoding="utf-8",
            )
            tools_dir = root / "tools"
            metadata_dir = root / "build-metadata"
            output_file = root / "github_output.txt"

            fake_src = types.ModuleType("src")
            fake_src.utils = types.ModuleType("src.utils")
            fake_src.patch_bundle_cache = types.ModuleType("src.patch_bundle_cache")
            fake_src.patch_bundle_cache.restore_cached_bundle = mock.Mock(return_value=None)

            def mock_detect(user: str, repo: str, tag: str, **kwargs):
                if user == "bad-user":
                    raise RuntimeError("403 Forbidden: Repository access blocked")
                return {
                    "tag_name": "v1.0.0",
                    "assets": [{"name": "cli.jar", "browser_download_url": "https://example.com/cli.jar"}]
                    if repo == "morphe-cli"
                    else [{"name": "patches.mpp", "browser_download_url": "https://example.com/patches.mpp"}],
                }

            fake_src.utils.detect_github_release = mock_detect
            fake_download = mock.Mock(return_value=True)

            with (
                mock.patch.dict(
                    sys.modules,
                    {"src": fake_src, "src.utils": fake_src.utils},
                ),
                mock.patch.dict(
                    os.environ,
                    {
                        "SOURCE_TAGS_JSON": json.dumps({"good": "v1.0.0", "bad": "v1.0.0"}),
                        "GITHUB_OUTPUT": str(output_file),
                    },
                    clear=False,
                ),
                mock.patch.object(download_all_tools, "SOURCES_DIR", sources_dir),
                mock.patch.object(download_all_tools, "TOOLS_DIR", tools_dir),
                mock.patch.object(download_all_tools, "download_asset", fake_download),
                mock.patch("pathlib.Path.cwd", return_value=root),
            ):
                result = download_all_tools.main()

            self.assertEqual(result, 0)
            self.assertTrue((tools_dir / "good").is_dir())
            self.assertFalse((tools_dir / "bad").is_dir())
            failures_path = Path("build-metadata/tool-download-failures.json")
            if failures_path.is_file():
                data = json.loads(failures_path.read_text(encoding="utf-8"))
                self.assertIn("bad", data)


if __name__ == "__main__":
    unittest.main()
