import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src import providers


class AppMetadataTests(unittest.TestCase):
    def test_aliexpress_uses_apkeditor_for_split_bundles(self) -> None:
        self.assertTrue(
            providers.merge_split_bundles_with_apkeditor("aliexpress")
        )

    def test_ibs_paint_requires_arm64_patch_library(self) -> None:
        metadata = providers.load_app_metadata("ibs_paint")

        self.assertEqual(metadata["source_policy"], "google-play-first")
        self.assertEqual(
            providers.required_apk_entries("ibs_paint"),
            ("lib/arm64-v8a/libibispaint.so",),
        )

    def test_metadata_supplies_package_and_play_only_policy(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            metadata_dir = Path(directory)
            (metadata_dir / "metadata-only-example.json").write_text(
                json.dumps(
                    {
                        "package": "com.example.app",
                        "source_policy": "google-play-only",
                    }
                ),
                encoding="utf-8",
            )
            with mock.patch.object(providers, "APP_METADATA_DIR", metadata_dir):
                self.assertEqual(
                    providers.configured_package("metadata-only-example"),
                    "com.example.app",
                )
                self.assertTrue(providers.google_play_only("metadata-only-example"))

    def test_unknown_source_policy_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            metadata_dir = Path(directory)
            (metadata_dir / "bad-policy-example.json").write_text(
                json.dumps(
                    {
                        "package": "com.example.bad",
                        "source_policy": "some-special-case",
                    }
                ),
                encoding="utf-8",
            )
            with mock.patch.object(providers, "APP_METADATA_DIR", metadata_dir):
                with self.assertRaisesRegex(ValueError, "invalid source_policy"):
                    providers.load_app_metadata("bad-policy-example")

    def test_non_boolean_bundle_merge_setting_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            metadata_dir = Path(directory)
            (metadata_dir / "bad-merge-setting.json").write_text(
                json.dumps(
                    {
                        "package": "com.example.bad",
                        "merge_split_bundles_with_apkeditor": "yes",
                    }
                ),
                encoding="utf-8",
            )
            with mock.patch.object(providers, "APP_METADATA_DIR", metadata_dir):
                with self.assertRaisesRegex(
                    ValueError, "merge_split_bundles_with_apkeditor"
                ):
                    providers.load_app_metadata("bad-merge-setting")

    def test_google_play_first_is_preferred_but_not_exclusive(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            metadata_dir = Path(directory)
            (metadata_dir / "play-first-example.json").write_text(
                json.dumps(
                    {
                        "package": "com.example.playfirst",
                        "source_policy": "google-play-first",
                    }
                ),
                encoding="utf-8",
            )
            with mock.patch.object(providers, "APP_METADATA_DIR", metadata_dir):
                self.assertTrue(providers.google_play_first("play-first-example"))
                self.assertFalse(providers.google_play_only("play-first-example"))


if __name__ == "__main__":
    unittest.main()
