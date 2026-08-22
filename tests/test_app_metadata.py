import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src import providers


class AppMetadataTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
