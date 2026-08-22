import tempfile
import unittest
from pathlib import Path

from src import gplaydl_market_headers as market_headers


UPSTREAM_AUTH_FRAGMENT = '''def build_headers(auth: dict) -> dict[str, str]:
    device_info = auth.get("deviceInfoProvider", {})
    locale = "en_US"

    headers = {
        "Authorization": f"Bearer {auth['authToken']}",
        "Accept-Language": "en-US",
        "X-DFE-UserLanguages": locale,
    }
    return headers
'''


class GPlayDlMarketHeadersTests(unittest.TestCase):
    def test_patch_uses_auth_bundle_locale_generically(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "auth.py"
            path.write_text(UPSTREAM_AUTH_FRAGMENT, encoding="utf-8")

            self.assertTrue(market_headers.patch_auth_headers(path))
            patched = path.read_text(encoding="utf-8")

            self.assertIn('auth.get("locale")', patched)
            self.assertIn('device_info.get("localeString")', patched)
            self.assertIn('accept_language = locale.replace("_", "-")', patched)
            self.assertIn('"Accept-Language": accept_language', patched)
            self.assertIn('"X-DFE-UserLanguages": locale', patched)
            self.assertNotIn("ja_JP", patched)
            self.assertNotIn("44010", patched)
            self.assertNotIn("jp.japanpost", patched)

    def test_patch_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "auth.py"
            path.write_text(UPSTREAM_AUTH_FRAGMENT, encoding="utf-8")

            self.assertTrue(market_headers.patch_auth_headers(path))
            first = path.read_text(encoding="utf-8")
            self.assertFalse(market_headers.patch_auth_headers(path))
            self.assertEqual(path.read_text(encoding="utf-8"), first)

    def test_stale_upstream_shape_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "auth.py"
            path.write_text("def build_headers(auth):\n    return {}\n", encoding="utf-8")

            with self.assertRaisesRegex(RuntimeError, "upstream gplaydl changed"):
                market_headers.patch_auth_headers(path)


if __name__ == "__main__":
    unittest.main()
