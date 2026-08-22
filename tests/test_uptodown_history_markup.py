import unittest

from src import uptodown_direct
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


if __name__ == "__main__":
    unittest.main()
