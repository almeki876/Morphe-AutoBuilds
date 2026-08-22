import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class VerifiedAppVersionRegressionTests(unittest.TestCase):
    def test_amazon_32_13_2_100_uses_upstream_verified_version_code(self) -> None:
        # Provenance: rushiranpise/morphe-patches commit
        # fea61fca1f10b214884d21da3ead44d885a910ae updated Amazon Shopping to
        # AppTarget(version="32.13.2.100", versionCode=1241320216).
        config = json.loads(
            (ROOT / "apps" / "apkpure" / "amazon-shopping.json").read_text(
                encoding="utf-8"
            )
        )

        self.assertEqual(config["package"], "com.amazon.mShop.android.shopping")
        self.assertEqual(config["version"], "32.13.2.100")
        self.assertEqual(config["version_code"], "1241320216")


if __name__ == "__main__":
    unittest.main()
