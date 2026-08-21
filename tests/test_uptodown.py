from __future__ import annotations

import unittest
from datetime import datetime, timezone
from unittest import mock

from src import uptodown
from src.versioning import VersionCandidate


class UptodownTests(unittest.TestCase):
    def test_api_key_is_stable_within_same_utc_hour(self) -> None:
        first = uptodown._generate_api_key(
            datetime(2026, 8, 21, 7, 1, 2, tzinfo=timezone.utc)
        )
        second = uptodown._generate_api_key(
            datetime(2026, 8, 21, 7, 59, 59, tzinfo=timezone.utc)
        )
        next_hour = uptodown._generate_api_key(
            datetime(2026, 8, 21, 8, 0, 0, tzinfo=timezone.utc)
        )
        self.assertEqual(first, second)
        self.assertNotEqual(first, next_hour)

    @mock.patch("src.uptodown._api_get")
    def test_api_resolves_exact_package_and_version(self, api_get: mock.Mock) -> None:
        app_response = mock.Mock(status_code=200)
        app_response.json.return_value = {"data": {"appID": 12345}}
        versions_response = mock.Mock(status_code=200)
        versions_response.json.return_value = {
            "data": [
                {
                    "version": "32.13.2.100",
                    "versionCode": "1241320216",
                    "fileID": 67890,
                    "fileType": "xapk",
                    "sha256": "expected-sha",
                },
                {
                    "version": "32.13.0.100",
                    "versionCode": "1241320016",
                    "fileID": 11111,
                },
            ]
        }
        download_response = mock.Mock(status_code=200)
        download_response.json.return_value = {
            "data": {"downloadURL": "https://cdn.uptodown.example/exact.xapk"}
        }
        api_get.side_effect = [app_response, versions_response, download_response]

        link = uptodown._api_download_link_for_candidate(
            "com.amazon.mShop.android.shopping",
            VersionCandidate(name="32.13.2.100"),
        )

        self.assertEqual(link, "https://cdn.uptodown.example/exact.xapk")
        self.assertEqual(api_get.call_args_list[0].args[0], "/apps/byPackagename/com.amazon.mShop.android.shopping")
        self.assertIn("/v3/app/12345/device/1/compatible/versions", api_get.call_args_list[1].args[0])
        self.assertEqual(api_get.call_args_list[2].args[0], "/apps/12345/file/67890/downloadUrl?update=0")

    @mock.patch("src.uptodown._api_get")
    def test_api_search_fallback_requires_exact_package(self, api_get: mock.Mock) -> None:
        by_package = mock.Mock(status_code=404)
        search = mock.Mock(status_code=200)
        search.json.return_value = {
            "data": [
                {"appID": 111, "packageName": "com.amazon.mShop.android.shopping.beta"},
                {"appID": 222, "packageName": "com.amazon.mShop.android.shopping"},
            ]
        }
        api_get.side_effect = [by_package, search]

        app_id = uptodown._api_app_id("com.amazon.mShop.android.shopping")

        self.assertEqual(app_id, 222)
        self.assertEqual(
            api_get.call_args_list[1].args[0],
            "/v2/apps/search/com.amazon.mShop.android.shopping?page[limit]=5&page[offset]=0",
        )

    @mock.patch("src.uptodown._api_get")
    def test_api_search_fallback_rejects_near_package(self, api_get: mock.Mock) -> None:
        by_package = mock.Mock(status_code=200)
        by_package.json.return_value = {"data": {}}
        search = mock.Mock(status_code=200)
        search.json.return_value = {
            "data": [
                {"appID": 111, "packagename": "com.amazon.mShop.android.shopping.beta"}
            ]
        }
        api_get.side_effect = [by_package, search]

        self.assertIsNone(uptodown._api_app_id("com.amazon.mShop.android.shopping"))

    @mock.patch("src.uptodown._api_get")
    def test_api_does_not_substitute_nearby_version(self, api_get: mock.Mock) -> None:
        app_response = mock.Mock(status_code=200)
        app_response.json.return_value = {"data": {"appID": 12345}}
        versions_response = mock.Mock(status_code=200)
        versions_response.json.return_value = {
            "data": [
                {
                    "version": "32.13.0.100",
                    "versionCode": "1241320016",
                    "fileID": 11111,
                }
            ]
        }
        api_get.side_effect = [app_response, versions_response]

        self.assertIsNone(
            uptodown._api_download_link_for_candidate(
                "com.amazon.mShop.android.shopping",
                VersionCandidate(name="32.13.2.100"),
            )
        )

    @mock.patch("src.uptodown._api_download_link_for_candidate", return_value=None)
    @mock.patch("src.uptodown._download_url_from_page")
    @mock.patch("src.uptodown._download_page_matches_candidate")
    @mock.patch("src.uptodown.utils.cf_aware_get")
    def test_current_release_uses_download_page_before_archive_api(
        self,
        cf_aware_get: mock.Mock,
        download_page_matches: mock.Mock,
        download_url_from_page: mock.Mock,
        api_download_link: mock.Mock,
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
        api_download_link.assert_called_once()
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

    @mock.patch("src.uptodown._api_download_link_for_candidate", return_value=None)
    @mock.patch("src.uptodown._download_url_from_page")
    @mock.patch("src.uptodown._download_page_matches_candidate")
    @mock.patch("src.uptodown.utils.cf_aware_get")
    def test_older_release_still_uses_archive_api(
        self,
        cf_aware_get: mock.Mock,
        download_page_matches: mock.Mock,
        download_url_from_page: mock.Mock,
        api_download_link: mock.Mock,
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
        api_download_link.assert_called_once()
        download_url_from_page.assert_called_once_with(
            "https://amazon-shopping.en.uptodown.com/android/download/999"
        )


if __name__ == "__main__":
    unittest.main()
