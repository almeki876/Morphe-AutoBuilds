from __future__ import annotations

import unittest
from pathlib import Path


class IssueFormTests(unittest.TestCase):
    def test_issue_chooser_links_to_contribution_guide_without_blank_escape_hatch(self) -> None:
        config = Path(".github/ISSUE_TEMPLATE/config.yml").read_text(encoding="utf-8")
        self.assertIn("blank_issues_enabled: false", config)
        self.assertIn("Contributor guide", config)
        self.assertIn("CONTRIBUTING.md", config)
        self.assertIn("https://github.com/almeki876/Morphe-AutoBuilds/blob/main/CONTRIBUTING.md", config)

    def test_triage_forms_cover_primary_report_categories(self) -> None:
        root = Path(".github/ISSUE_TEMPLATE")
        expected = {
            "01_build_download.yml": "[Build]",
            "02_app_runtime.yml": "[App]",
            "03_actions_release.yml": "[CI]",
            "04_other_problem.yml": "[Other]",
            "feature_request.yml": "[Request]",
        }
        for filename, prefix in expected.items():
            with self.subTest(filename=filename):
                text = (root / filename).read_text(encoding="utf-8")
                self.assertIn("body:", text)
                self.assertIn(prefix, text)
                self.assertIn("required: true", text)

    def test_generic_bug_form_was_replaced_by_specific_triage_forms(self) -> None:
        self.assertFalse(Path(".github/ISSUE_TEMPLATE/bug_report.yml").exists())


if __name__ == "__main__":
    unittest.main()
