import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src import patch_bundle_cache
from scripts import update_patch_bundle_cache


class PatchBundleCacheTests(unittest.TestCase):
    def test_file_classification(self) -> None:
        self.assertTrue(patch_bundle_cache.is_patch_bundle_file("patches-1.0.0.mpp"))
        self.assertTrue(patch_bundle_cache.is_patch_bundle_file("bundle.rvp"))
        self.assertTrue(patch_bundle_cache.is_patch_bundle_file("revanced-patches-4.0.jar"))
        self.assertTrue(patch_bundle_cache.is_patch_bundle_file("patches-list.json"))
        self.assertFalse(patch_bundle_cache.is_patch_bundle_file("morphe-cli.jar"))
        self.assertFalse(patch_bundle_cache.is_patch_bundle_file("something.txt"))

        self.assertTrue(patch_bundle_cache.is_cacheable_tool_file("morphe-cli.jar"))
        self.assertTrue(patch_bundle_cache.is_cacheable_tool_file("patches-1.0.0.mpp"))
        self.assertFalse(patch_bundle_cache.is_cacheable_tool_file("cli-sources.jar"))
        self.assertFalse(patch_bundle_cache.is_cacheable_tool_file("cli-javadoc.jar"))

    def test_asset_name_roundtrip(self) -> None:
        name = patch_bundle_cache.make_asset_name("morphe", "v2.4.0", "patches.mpp")
        self.assertEqual(name, "morphe--v2.4.0--patches.mpp")
        parsed = patch_bundle_cache.parse_asset_name(name)
        self.assertEqual(parsed, ("morphe", "v2.4.0", "patches.mpp"))

        self.assertIsNone(patch_bundle_cache.parse_asset_name("invalid_asset_name.mpp"))

    def test_restore_cached_bundle_via_gh(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dest_dir = Path(tmp)
            # Create a fake downloaded asset with prefix
            cached_asset = dest_dir / "morphe--v2.4.0--patches.mpp"
            cached_asset.write_bytes(b"fake mpp content")

            fake_run = mock.Mock(return_value=mock.Mock(returncode=0))
            with mock.patch("subprocess.run", fake_run):
                restored = patch_bundle_cache.restore_cached_bundle(
                    "morphe", dest_dir, repository="owner/repo"
                )

            self.assertIn("patches.mpp", restored)
            self.assertTrue((dest_dir / "patches.mpp").is_file())
            self.assertEqual((dest_dir / "patches.mpp").read_bytes(), b"fake mpp content")

    def test_update_cache_prunes_older_tags_and_uploads_new(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tools_dir = Path(tmp) / "tools"
            tools_dir.mkdir()
            source_dir = tools_dir / "morphe"
            source_dir.mkdir()
            mpp_file = source_dir / "patches-v2.mpp"
            mpp_file.write_bytes(b"content")

            old_asset = mock.Mock()
            old_asset.name = "morphe--v1.0.0--patches-v1.mpp"
            old_asset.delete_asset = mock.Mock()

            fake_release = mock.Mock()
            fake_release.get_assets.return_value = [old_asset]

            fake_repo = mock.Mock()
            fake_repo.get_releases.return_value = [fake_release]
            fake_release.tag_name = patch_bundle_cache.CACHE_TAG

            fake_gh = mock.Mock()
            fake_gh.get_repo.return_value = fake_repo

            with (
                mock.patch.object(update_patch_bundle_cache, "TOOLS_DIR", tools_dir),
                mock.patch.object(update_patch_bundle_cache, "_get_github", return_value=fake_gh),
                mock.patch.object(update_patch_bundle_cache, "upload_with_retry", return_value=True) as fake_upload,
                mock.patch.dict(
                    "os.environ",
                    {
                        "GITHUB_TOKEN": "test_token",
                        "GITHUB_REPOSITORY": "owner/repo",
                        "SOURCE_TAGS_JSON": json.dumps({"morphe": "v2.0.0"}),
                    },
                    clear=False,
                ),
            ):
                result = update_patch_bundle_cache.main()

            self.assertEqual(result, 0)
            fake_upload.assert_called_once_with(
                fake_release, mpp_file, "morphe--v2.0.0--patches-v2.mpp"
            )
            old_asset.delete_asset.assert_called_once()


if __name__ == "__main__":
    unittest.main()
