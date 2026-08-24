import os
import unittest
from unittest import mock

from src import apk_cache, cache_contract


class ApkCacheContractTests(unittest.TestCase):
    def test_legacy_workflow_tag_is_overridden_before_cache_namespace_is_selected(self) -> None:
        with mock.patch.dict(os.environ, {"BASE_APK_CACHE_TAG": "base-apk-cache-v2"}, clear=False):
            cache_contract.enforce()
            self.assertEqual(os.environ["BASE_APK_CACHE_TAG"], cache_contract.CACHE_CONTRACT_TAG)

    def test_cache_module_uses_contract_namespace(self) -> None:
        self.assertEqual(apk_cache.CACHE_TAG, cache_contract.CACHE_CONTRACT_TAG)

    def test_current_asset_contract_includes_delivery_profile(self) -> None:
        name = (
            apk_cache._asset_prefix("com.example.app", "1.2.3")
            + ("0" * 64)
            + "--dp_gplay-ja-jp-px9a-split-v1.apks"
        )
        self.assertEqual(
            apk_cache.parse_asset_name(name),
            (
                "com.example.app",
                "1.2.3",
                "0" * 64,
                ".apks",
            ),
        )
        self.assertEqual(
            apk_cache._asset_profile(name),
            "gplay-ja-jp-px9a-split-v1",
        )


if __name__ == "__main__":
    unittest.main()
