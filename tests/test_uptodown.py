from __future__ import annotations

import unittest
from unittest import mock

from src import uptodown
from src.versioning import VersionCandidate


class UptodownTests(unittest.TestCase):
    @mock.patch("src.uptodown._download_url_from_page")
    @mock.patch("src.uptodown._download_page_matches_candidate")
    @mock.patch("src.uptodown.utils.cf_aware_get")
    def test_current_release_uses_download_page_before_archive_api(
        self,
        cf_aware_get: mock.Mock,
        download_page_matches: mock.Mock,
        download_url_from_page: mock.Mock,
    ) -> None:
        download_page_matches.return_value = True
        download_url_from_page.return_value = "https://dw.uptodown.com/dwn/file-token"

        link = uptodown.get_download_link(
            "32.13.2.100",
            "amazon-shopping",
            {
                "name": "amazon-shopping",
                "package": "com.amazon.mShop.android.shopping",
            },
            candidate=VersionCandidate(name="32.13.2.100"),
        )

        self.assertEqual(link, "https://dw.uptodown.com/dwn/file-token")
        download_page_matches.assert_called_once_with(
            "https://amazon-shopping.en.uptodown.com/android/download",
            VersionCandidate(name="32.13.2.100"),
        )
        download_url_from_page.assert_called_once_with(
            "https://amazon-shopping.en.uptodown.com/android/download"
        )
        cf_aware_get.assert_not_called()

    @mock.patch("src.uptodown.utils.cf_aware_get")
    def test_download_page_metadata_matches_current_release_without_versions_list(
        self,
        cf_aware_get: mock.Mock,
    ) -> None:
        response = mock.Mock()
        response.status_code = 200
        response.content = b"""
            <html>
              <head>
                <title>Download Amazon Shopping 32.13.2.100 for Android | Uptodown</title>
                <meta property='og:title' content='Amazon Shopping 32.13.2.100'>
              </head>
              <body><h1 id='detail-app-name'>Amazon Shopping</h1></body>
            </html>
        """
        cf_aware_get.return_value = response

        self.assertTrue(
            uptodown._download_page_matches_candidate(
                "https://amazon-shopping.en.uptodown.com/android/download",
                VersionCandidate(name="32.13.2.100"),
            )
        )

    @mock.patch("src.uptodown.utils.cf_aware_get")
    def test_download_page_metadata_does_not_match_older_release_in_body(
        self,
        cf_aware_get: mock.Mock,
    ) -> None:
        response = mock.Mock()
        response.status_code = 200
        response.content = b"""
            <html>
              <head><title>Download Amazon Shopping 32.13.2.100 for Android | Uptodown</title></head>
              <body>
                <h1 id='detail-app-name'>Amazon Shopping</h1>
                <div>Older versions: 32.13.0.100</div>
              </body>
            </html>
        """
        cf_aware_get.return_value = response

        self.assertFalse(
            uptodown._download_page_matches_candidate(
                "https://amazon-shopping.en.uptodown.com/android/download",
                VersionCandidate(name="32.13.0.100"),
            )
        )

    @mock.patch("src.uptodown._download_url_from_page")
    @mock.patch("src.uptodown._download_page_matches_candidate")
    @mock.patch("src.uptodown.utils.cf_aware_get")
    def test_older_release_still_uses_archive_api(
        self,
        cf_aware_get: mock.Mock,
        download_page_matches: mock.Mock,
        download_url_from_page: mock.Mock,
    ) -> None:
        download_page_matches.return_value = False
        versions_page = mock.Mock()
        versions_page.status_code = 200
        versions_page.content = b"""
            <h1 id='detail-app-name' data-code='123'>Amazon Shopping</h1>
            <div id='versions-items-list'>
              <span class='version'>32.13.2.100</span>
              <span class='version'>32.13.0.100</span>
            </div>
        """
        archive = mock.Mock()
        archive.raise_for_status.return_value = None
        archive.json.return_value = {
            "data": [
                {
                    "version": "32.13.0.100",
                    "versionURL": {
                        "url": "https://amazon-shopping.en.uptodown.com/android",
                        "extraURL": "download",
                        "versionID": "999",
                    },
                }
            ]
        }
        cf_aware_get.side_effect = [versions_page, archive]
        download_url_from_page.return_value = "https://dw.uptodown.com/dwn/old-token"

        link = uptodown.get_download_link(
            "32.13.0.100",
            "amazon-shopping",
            {
                "name": "amazon-shopping",
                "package": "com.amazon.mShop.android.shopping",
            },
            candidate=VersionCandidate(name="32.13.0.100"),
        )

        self.assertEqual(link, "https://dw.uptodown.com/dwn/old-token")
        download_url_from_page.assert_called_once_with(
            "https://amazon-shopping.en.uptodown.com/android/download/999"
        )


if __name__ == "__main__":
    unittest.main()
