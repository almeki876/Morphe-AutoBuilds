import unittest

from src import browser_fallback, uptodown_direct
from src.versioning import VersionCandidate


class UptodownHistoryMarkupTests(unittest.TestCase):
    def test_history_card_matches_v_version_markup(self) -> None:
        soup = uptodown_direct.BeautifulSoup(
            """
            <div data-version-id="424242"
                 data-url="https://crunchyroll.en.uptodown.com/android"
                 data-extra-url="download">
              <span class="type">xapk</span>
              <div class="v-version">3.112.2</div>
              <div>Android + 8.0</div>
            </div>
            """,
            "html.parser",
        )
        card = soup.select_one("[data-version-id]")
        self.assertIsNotNone(card)
        assert card is not None
        self.assertTrue(
            uptodown_direct._history_card_matches_candidate(
                card, VersionCandidate(name="3.112.2")
            )
        )

    def test_history_card_matches_plain_text_without_version_class(self) -> None:
        soup = uptodown_direct.BeautifulSoup(
            """
            <article data-version-id="987654"
                     data-url="https://ibispaint-x.en.uptodown.com/android"
                     data-extra-url="download">
              <span>xapk 14.0.6 Android + 7.0 Jul 6, 2026</span>
            </article>
            """,
            "html.parser",
        )
        card = soup.select_one("[data-version-id]")
        self.assertIsNotNone(card)
        assert card is not None
        self.assertTrue(
            uptodown_direct._history_card_matches_candidate(
                card, VersionCandidate(name="14.0.6")
            )
        )

    def test_history_card_does_not_prefix_match_newer_version(self) -> None:
        soup = uptodown_direct.BeautifulSoup(
            '<div data-version-id="1">xapk 3.112.20 Android + 8.0</div>',
            "html.parser",
        )
        card = soup.select_one("[data-version-id]")
        self.assertIsNotNone(card)
        assert card is not None
        self.assertFalse(
            uptodown_direct._history_card_matches_candidate(
                card, VersionCandidate(name="3.112.2")
            )
        )

    def test_browser_history_card_builds_exact_release_page(self) -> None:
        target = {
            "dataVersionId": "424242",
            "dataExtraUrl": "download",
            "dataUrl": "https://crunchyroll.en.uptodown.com/android",
            "href": "",
        }
        self.assertEqual(
            browser_fallback._version_target_page_url(
                target,
                "https://crunchyroll.en.uptodown.com/android/versions",
            ),
            "https://crunchyroll.en.uptodown.com/android/download/424242",
        )

    def test_browser_history_card_keeps_concrete_data_url(self) -> None:
        target = {
            "dataVersionId": "424242",
            "dataExtraUrl": "download",
            "dataUrl": "https://crunchyroll.en.uptodown.com/android/download/424242",
            "href": "",
        }
        self.assertEqual(
            browser_fallback._version_target_page_url(
                target,
                "https://crunchyroll.en.uptodown.com/android/versions",
            ),
            "https://crunchyroll.en.uptodown.com/android/download/424242",
        )

    def test_browser_rejects_generic_download_page_as_binary(self) -> None:
        target = {
            "dataUrl": "",
            "href": "https://adobe-lightroom-mobile.en.uptodown.com/android/download",
            "onclick": "",
        }
        self.assertIsNone(
            browser_fallback._direct_url_from_target(
                target,
                "https://adobe-lightroom-mobile.en.uptodown.com/android/download/123",
            )
        )

    def test_browser_accepts_only_uptodown_cdn_download(self) -> None:
        target = {
            "dataUrl": "opaque-token-abcdefghijklmnopqrstuvwxyz",
            "href": "",
            "onclick": "",
        }
        direct = browser_fallback._direct_url_from_target(
            target,
            "https://crunchyroll.en.uptodown.com/android/download/424242",
        )
        self.assertEqual(
            direct,
            "https://dw.uptodown.com/dwn/opaque-token-abcdefghijklmnopqrstuvwxyz",
        )
        self.assertFalse(
            browser_fallback._is_direct_uptodown_file_url(
                "https://example.invalid/dwn/opaque-token-abcdefghijklmnopqrstuvwxyz"
            )
        )

    def test_browser_rejects_external_history_root(self) -> None:
        target = {
            "dataVersionId": "7",
            "dataExtraUrl": "download",
            "dataUrl": "https://example.invalid/android",
            "href": "",
        }
        self.assertIsNone(
            browser_fallback._version_target_page_url(
                target,
                "https://crunchyroll.en.uptodown.com/android/versions",
            )
        )


if __name__ == "__main__":
    unittest.main()
