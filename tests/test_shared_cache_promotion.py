import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import stage_shared_base_apk_cache as stage_cache


class SharedCachePromotionTests(unittest.TestCase):
    def test_only_google_play_origin_is_cacheable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manifest_dir = Path(tmp)
            manifest = manifest_dir / "manifest.json"
            manifest.write_text("{}", encoding="utf-8")
            origin = manifest_dir / "origin.json"

            origin.write_text(json.dumps({"provider": "apkmirror"}), encoding="utf-8")
            self.assertIsNone(stage_cache._cache_provider(manifest))

            origin.write_text(json.dumps({"provider": "aurora-google-play"}), encoding="utf-8")
            self.assertEqual(stage_cache._cache_provider(manifest), "aurora-google-play")

            origin.write_text(json.dumps({"provider": "google-play"}), encoding="utf-8")
            self.assertEqual(stage_cache._cache_provider(manifest), "google-play")

    def test_missing_origin_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manifest = Path(tmp) / "manifest.json"
            manifest.write_text("{}", encoding="utf-8")
            self.assertIsNone(stage_cache._cache_provider(manifest))


if __name__ == "__main__":
    unittest.main()
