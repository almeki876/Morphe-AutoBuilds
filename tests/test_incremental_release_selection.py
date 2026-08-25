import unittest
from pathlib import Path

from scripts import release_notes


class IncrementalReleaseSelectionTests(unittest.TestCase):
    ITEMS = [
        {"app_name": "youtube", "source": "revanced-anddea"},
        {"app_name": "youtube-music", "source": "revanced-anddea"},
        {"app_name": "amazon-shopping", "source": "rushiranpise"},
        {"app_name": "yuucho-tsucho", "source": "rushiranpise"},
    ]

    def test_apk_update_does_not_expand_to_every_app_in_its_patch_source(self):
        selected = release_notes._requested_matrix(
            self.ITEMS,
            {
                "updated_apps": "amazon-shopping",
                "updated_sources": "",
                "build_all_sources": "false",
            },
        )
        self.assertEqual(
            {(item["app_name"], item["source"]) for item in selected},
            {("amazon-shopping", "rushiranpise")},
        )

    def test_anddea_and_apk_updates_form_a_narrow_union(self):
        selected = release_notes._requested_matrix(
            self.ITEMS,
            {
                "updated_apps": "amazon-shopping",
                "updated_sources": "revanced-anddea",
                "build_all_sources": "false",
            },
        )
        self.assertEqual(
            {(item["app_name"], item["source"]) for item in selected},
            {
                ("youtube", "revanced-anddea"),
                ("youtube-music", "revanced-anddea"),
                ("amazon-shopping", "rushiranpise"),
            },
        )

    def test_release_title_has_no_partial_marker(self):
        workflow = Path(".github/workflows/build.yml").read_text(encoding="utf-8")
        self.assertNotIn("(Partial)", workflow)

    def test_apk_updates_are_not_expanded_back_to_patch_sources(self):
        workflow = Path(".github/workflows/check-upstream.yml").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("for forced_source in", workflow)
        self.assertIn('-f updated_apps="$UPDATED_APPS"', workflow)


if __name__ == "__main__":
    unittest.main()
