from __future__ import annotations

import unittest
from unittest import mock

from bs4 import BeautifulSoup

from src import apkmirror_latest


class APKMirrorLatestTests(unittest.TestCase):
    @mock.patch("src.apkmirror_latest._base._uploads_url", return_value=None)
    @mock.patch("src.apkmirror_latest._base._discovery_page")
    @mock.patch("src.apkmirror_latest._base._search_url")
    @mock.patch("src.apkmirror_latest._base._configured_app_url")
    @mock.patch("src.apkmirror_latest._base._configured_uploads_url")
    def test_newer_canonical_release_beats_stale_first_feed(
        self,
        configured_uploads_url: mock.Mock,
        configured_app_url: mock.Mock,
        search_url: mock.Mock,
        discovery_page: mock.Mock,
        uploads_url: mock.Mock,
    ) -> None:
        configured_uploads_url.return_value = "https://www.apkmirror.com/uploads/?appcategory=brave-browser-beta"
        configured_app_url.return_value = "https://www.apkmirror.com/apk/brave-software/brave-browser-beta/"
        search_url.side_effect = lambda query: f"https://www.apkmirror.com/?post_type=app_release&searchtype=apk&s={query}"

        stale = BeautifulSoup(
            "<a href='/apk/brave-software/brave-browser-beta/brave-browser-beta-1-89-116-release/'>Brave Browser Beta 1.89.116</a>",
            "html.parser",
        )
        current = BeautifulSoup(
            "<a href='/apk/brave-software/brave-browser-beta/brave-browser-beta-1-94-112-release/'>Brave Browser Beta 1.94.112</a>",
            "html.parser",
        )
        discovery_page.side_effect = [
            (stale, configured_uploads_url.return_value),
            (current, configured_app_url.return_value),
            None,
            None,
            None,
        ]

        with mock.patch.object(apkmirror_latest._base, "_DISCOVERY_BLOCKED", False):
            latest = apkmirror_latest.get_latest_version(
                "brave-beta",
                {
                    "org": "brave-software",
                    "name": "brave-browser-beta",
                    "package": "com.brave.browser_beta",
                },
            )

        self.assertEqual(latest, "1.94.112")

    def test_other_provider_operations_delegate_to_base_module(self) -> None:
        self.assertIs(
            apkmirror_latest.get_download_link_for_candidate,
            apkmirror_latest._base.get_download_link_for_candidate,
        )


if __name__ == "__main__":
    unittest.main()
