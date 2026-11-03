import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"


class RepositoryOrganizationTests(unittest.TestCase):
    IMPORTANT_WORKFLOW_NAMES = {
        "build.yml": "Build and Release APKs",
        "build-all-apps.yml": "Build All Apps",
        "configuration-check.yml": "Repository Validation",
        "operations.yml": "Repository Operations",
    }

    def _workflow_name(self, path: Path) -> str:
        first_line = path.read_text(encoding="utf-8").splitlines()[0]
        match = re.fullmatch(r'name:\s*["\']?(.*?)["\']?', first_line)
        self.assertIsNotNone(match, f"workflow has no readable name: {path}")
        return match.group(1)

    def test_important_workflows_have_stable_human_readable_names(self):
        self.assertEqual(
            {path.name for path in WORKFLOWS.glob("*.yml")},
            set(self.IMPORTANT_WORKFLOW_NAMES),
        )
        for filename, expected in self.IMPORTANT_WORKFLOW_NAMES.items():
            path = WORKFLOWS / filename
            self.assertTrue(path.exists(), f"required workflow missing: {filename}")
            self.assertEqual(self._workflow_name(path), expected)

    def test_manual_full_build_has_a_separate_simple_entry_point(self):
        text = (WORKFLOWS / "build-all-apps.yml").read_text(encoding="utf-8")
        self.assertIn("workflow_dispatch:", text)
        self.assertIn("gh workflow run build.yml", text)
        self.assertIn("build_all_sources=true", text)

    def test_maintenance_tasks_share_one_workflow_entry(self):
        text = (WORKFLOWS / "operations.yml").read_text(encoding="utf-8")
        for task in (
            "check-upstream",
            "provider-health",
            "refresh-download-links",
            "diagnose-google-play",
            "verify-japan-egress",
            "rebuild-release-details",
            "register-google-play",
        ):
            self.assertIn(f"- {task}", text)
        self.assertIn("schedule:", text)

        for removed in (
            "check-upstream.yml",
            "health-check.yml",
            "diagnose-google-play-purchase.yml",
            "japan-egress-check.yml",
            "publish-release-details.yml",
            "register-google-play.yml",
            "update-direct-download-links.yml",
        ):
            self.assertFalse((WORKFLOWS / removed).exists())

    def test_direct_download_catalog_is_updated_by_operations_and_build_state(self):
        text = (WORKFLOWS / "operations.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("schedule:", text)
        self.assertIn("workflow_dispatch:", text)
        self.assertIn("Morphe-AutoBuilds-Direct-Download.md", text)
        self.assertIn("Morphe-AutoBuilds-Direct-Download.ja.md", text)

        state_script = (ROOT / "scripts" / "save_successful_state.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("DIRECT_DOWNLOAD_JA_FILE", state_script)

    def test_removed_legacy_helpers_stay_removed(self):
        self.assertFalse((ROOT / "scripts" / "download_reused_apks.py").exists())
        self.assertFalse((ROOT / "scripts" / "pr_build_scope.py").exists())

    def test_readme_is_user_facing_and_setup_owns_operations(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        setup = (ROOT / "SETUP.md").read_text(encoding="utf-8")

        self.assertIn("Download APKs", readme)
        self.assertIn("SETUP.md", readme)
        self.assertIn("SETUP.ja.md", readme)
        self.assertNotIn("GPLAY_AAS_TOKEN", readme)
        self.assertNotIn("TS_OAUTH_SECRET", readme)
        self.assertNotIn("version_code` を設定ファイル", readme)

        self.assertIn("Required GitHub Secrets and Variables", setup)
        self.assertIn("Google Play download flow", setup)
        self.assertIn("APK download fallback", setup)
        self.assertIn("probe_apk_sources.py", setup)
        self.assertTrue((ROOT / "SETUP.ja.md").exists())

    def test_python_workflows_share_the_repository_setup_action(self):
        workflows = list(WORKFLOWS.glob("*.yml"))
        self.assertTrue(workflows)
        for path in workflows:
            text = path.read_text(encoding="utf-8")
            self.assertNotIn(
                "uses: actions/setup-python@",
                text,
                f"{path.name} should use the shared Python setup action",
            )

        action = (
            ROOT / ".github" / "actions" / "setup-python-project" / "action.yml"
        ).read_text(encoding="utf-8")
        self.assertIn("install-dependencies:", action)
        self.assertIn("inputs.install-dependencies == 'true'", action)
        self.assertIn("python-version: '3.11'", action)

    def test_issue_forms_support_bilingual_reports_and_safe_attachments(self):
        issue_templates = ROOT / ".github" / "ISSUE_TEMPLATE"
        forms = [
            issue_templates / "01_build_download.yml",
            issue_templates / "02_app_runtime.yml",
            issue_templates / "03_actions_release.yml",
            issue_templates / "04_other_problem.yml",
            issue_templates / "feature_request.yml",
        ]
        for path in forms:
            text = path.read_text(encoding="utf-8")
            self.assertIn("English and Japanese", text, path.name)
            self.assertIn("Do not attach APK files", text, path.name)
            self.assertIn("アクセストークン", text, path.name)

        config = (issue_templates / "config.yml").read_text(encoding="utf-8")
        self.assertIn("CONTRIBUTING.md", config)

    def test_contributor_guides_are_bilingual_and_document_required_checks(self):
        english = (ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
        japanese = (ROOT / "CONTRIBUTING.ja.md").read_text(encoding="utf-8")
        for text in (english, japanese):
            self.assertIn("unittest discover tests", text)
            self.assertIn("actionlint", text)
        self.assertIn("CONTRIBUTING.ja.md", english)
        self.assertIn("CONTRIBUTING.md", japanese)


if __name__ == "__main__":
    unittest.main()
