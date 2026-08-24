import json
import tempfile
import unittest
from pathlib import Path

from scripts import stage_shared_base_apk_cache as stage_cache


class SharedCachePromotionTests(unittest.TestCase):
    def test_provider_is_provenance_not_eligibility(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manifest = Path(tmp) / "manifest.json"
            manifest.write_text("{}", encoding="utf-8")
            origin = Path(tmp) / "origin.json"

            for provider in ("apkmirror", "aurora-google-play", "google-play", "official-site", "unknown"):
                origin.write_text(json.dumps({"provider": provider}), encoding="utf-8")
                self.assertEqual(stage_cache._cache_provider(manifest), provider)

    def test_missing_origin_uses_unknown_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manifest = Path(tmp) / "manifest.json"
            manifest.write_text("{}", encoding="utf-8")
            self.assertEqual(stage_cache._cache_provider(manifest), "unknown")


if __name__ == "__main__":
    unittest.main()
