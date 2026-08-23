import unittest
from pathlib import Path

from scripts import pr_build_scope, release_notes


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
                "updated_sources": "anddea",
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

    def test_pr_app_config_change_builds_only_that_app(self):
        apps, sources = pr_build_scope.select_scope(
            ["apps/apkpure/amazon-shopping.json"]
        )
        self.assertEqual(apps, {"amazon-shopping"})
        self.assertEqual(sources, set())

    def test_pr_source_change_builds_only_that_source(self):
        apps, sources = pr_build_scope.select_scope(
            ["sources/revanced-anddea.json"]
        )
        self.assertEqual(apps, set())
        self.assertEqual(sources, {"revanced-anddea"})

    def test_app_specific_patch_list_builds_only_that_app(self):
        apps, sources = pr_build_scope.select_scope(
            ["patches/youtube-revanced-anddea.txt"],
            {"youtube", "youtube-music"},
        )
        self.assertEqual(apps, {"youtube"})
        self.assertEqual(sources, set())

    def test_unknown_patch_list_uses_one_smoke_app(self):
        apps, sources = pr_build_scope.select_scope(
            ["patches/future-app-future-source.txt"],
            {"youtube"},
        )
        self.assertEqual(apps, {"crunchyroll"})
        self.assertEqual(sources, set())

    def test_core_change_uses_one_smoke_app(self):
        apps, sources = pr_build_scope.select_scope(["scripts/prepare_matrix.py"])
        self.assertEqual(apps, {"crunchyroll"})
        self.assertEqual(sources, set())

    def test_tests_only_change_does_not_dispatch_an_apk_build(self):
        apps, sources = pr_build_scope.select_scope(["tests/test_example.py"])
        self.assertEqual(apps, set())
        self.assertEqual(sources, set())

    def test_release_title_has_no_partial_marker(self):
        workflow = Path(".github/workflows/build.yml").read_text(encoding="utf-8")
        self.assertNotIn("(Partial)", workflow)

    def test_apk_updates_are_not_expanded_back_to_patch_sources(self):
        workflow = Path(".github/workflows/check-upstream.yml").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("for forced_source in", workflow)
        self.assertIn('-f updated_apps="$UPDATED_APPS"', workflow)

    def test_pr_verification_uses_computed_scope(self):
        workflow = Path(
            ".github/workflows/pr-targeted-build-verification.yml"
        ).read_text(encoding="utf-8")
        self.assertIn("scripts/pr_build_scope.py", workflow)
        self.assertIn('-f updated_apps="$UPDATED_APPS"', workflow)
        self.assertNotIn("adobe-acrobat,amazon-shopping,crunchyroll", workflow)


if __name__ == "__main__":
    unittest.main()
