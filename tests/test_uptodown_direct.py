from __future__ import annotations

import unittest
from unittest import mock

from src import uptodown_direct
from src.versioning import VersionCandidate


class UptodownDirectTests(unittest.TestCase):
    @mock.patch("src.uptodown_direct.legacy._download_url_from_page")
    @mock.patch("src.uptodown_direct.utils.cf_aware_get")
    def test_all_variants_resolves_exact_xapk(
        self,
        cf_aware_get: mock.Mock,
        download_url_from_page: mock.Mock,
    ) -> None:
        download_page = mock.Mock(status_code=200)
        download_page.content = b"""
            <html>
              <head><title>Download Amazon Shopping 32.13.2.100 for Android | Uptodown</title></head>
              <body>
                <h1 id='detail-app-name' data-code='12345'>Amazon Shopping</h1>
                <div class='version'>32.13.2.100</div>
                <button class='button variants' data-version='9988'>All variants</button>
              </body>
            </html>
        """
        files = mock.Mock(status_code=200)
        files.json.return_value = {
            "content": """
                <div class='variant'>
                  <div class='v-file'><span>xapk</span></div>
                  <div class='v-report' data-file-id='777'></div>
                </div>
            """
        }
        cf_aware_get.side_effect = [download_page, files]
        download_url_from_page.return_value = (
            "https://dw.uptodown.com/dwn/exact-amazon-shopping-token"
        )

        link = uptodown_direct._direct_link_from_variants(
            VersionCandidate(name="32.13.2.100"),
            "amazon-shopping",
            {
                "name": "amazon-shopping",
                "package": "com.amazon.mShop.android.shopping",
            },
        )

        self.assertEqual(
            link,
            "https://dw.uptodown.com/dwn/exact-amazon-shopping-token",
        )
        self.assertEqual(
            cf_aware_get.call_args_list[1].args[0],
            "https://amazon-shopping.en.uptodown.com/app/12345/version/9988/files",
        )
        download_url_from_page.assert_called_once_with(
            "https://amazon-shopping.en.uptodown.com/android/download/777-x"
        )

    @mock.patch(
        "src.uptodown_direct.legacy.get_download_link_for_candidate",
        return_value="https://legacy.example/exact",
    )
    @mock.patch("src.uptodown_direct._direct_link_from_variants")
    def test_candidate_prefers_all_variants_for_configured_slug(
        self,
        variants_resolver: mock.Mock,
        legacy_resolver: mock.Mock,
    ) -> None:
        candidate = VersionCandidate(name="32.13.2.100")
        variants_resolver.return_value = "https://dw.uptodown.com/dwn/exact"
        config = {
            "name": "amazon-shopping",
            "package": "com.amazon.mShop.android.shopping",
        }

        link = uptodown_direct.get_download_link_for_candidate(
            candidate,
            "amazon-shopping",
            config,
        )

        self.assertEqual(link, "https://dw.uptodown.com/dwn/exact")
        variants_resolver.assert_called_once_with(
            candidate,
            "amazon-shopping",
            config,
        )
        legacy_resolver.assert_not_called()

    @mock.patch(
        "src.uptodown_direct.legacy.get_download_link_for_candidate",
        return_value="https://legacy.example/exact",
    )
    @mock.patch("src.uptodown_direct._direct_link_from_variants", return_value=None)
    def test_candidate_uses_legacy_after_direct_failure(
        self,
        variants_resolver: mock.Mock,
        legacy_resolver: mock.Mock,
    ) -> None:
        candidate = VersionCandidate(name="32.13.2.100")
        config = {
            "name": "amazon-shopping",
            "package": "com.amazon.mShop.android.shopping",
        }

        link = uptodown_direct.get_download_link_for_candidate(
            candidate,
            "amazon-shopping",
            config,
        )

        self.assertEqual(link, "https://legacy.example/exact")
        variants_resolver.assert_called_once_with(
            candidate,
            "amazon-shopping",
            config,
        )
        legacy_resolver.assert_called_once_with(
            candidate,
            "amazon-shopping",
            config,
        )

    def test_configured_slug_avoids_generic_guesses(self) -> None:
        self.assertEqual(
            uptodown_direct._configured_slugs(
                {
                    "name": "amazon-shopping",
                    "package": "com.amazon.mShop.android.shopping",
                }
            ),
            ["amazon-shopping"],
        )


if __name__ == "__main__":
    unittest.main()
