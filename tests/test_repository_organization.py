import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"


class RepositoryOrganizationTests(unittest.TestCase):
    EXPECTED_WORKFLOW_NAMES = {
        "build.yml": "Build and Release APKs",
        "build-all-apps.yml": "手動: 全アプリをビルド",
        "check-upstream.yml": "自動: アップストリーム更新を確認",
        "close-resolved-build-issues.yml": "Close Resolved Build Issues",
        "configuration-check.yml": "CI: 設定・テスト検証",
        "diagnose-google-play-purchase.yml": "保守: Google Play取得を診断",
        "health-check.yml": "保守: 取得元とビルド環境を点検",
        "japan-egress-check.yml": "保守: 日本Tailscale経路を確認",
        "publish-release-details.yml": "Publish Release Details",
        "publish-virustotal-cache.yml": "自動: VirusTotalキャッシュを保存",
        "register-google-play.yml": "セットアップ: Google Playアカウントを登録",
        "update-direct-download-links.yml": "自動: APK直リンク一覧を更新",
    }

    def _workflow_name(self, path: Path) -> str:
        first_line = path.read_text(encoding="utf-8").splitlines()[0]
        match = re.fullmatch(r'name:\s*["\']?(.*?)["\']?', first_line)
        self.assertIsNotNone(match, f"workflow has no readable name: {path}")
        return match.group(1)

    def test_workflow_files_have_stable_human_readable_names(self):
        actual_files = {path.name for path in WORKFLOWS.glob("*.yml")}
        self.assertEqual(actual_files, set(self.EXPECTED_WORKFLOW_NAMES))
        for filename, expected in self.EXPECTED_WORKFLOW_NAMES.items():
            self.assertEqual(self._workflow_name(WORKFLOWS / filename), expected)

    def test_workflow_run_dependencies_still_target_primary_build_name(self):
        for filename in (
            "close-resolved-build-issues.yml",
            "publish-release-details.yml",
            "publish-virustotal-cache.yml",
            "update-direct-download-links.yml",
        ):
            text = (WORKFLOWS / filename).read_text(encoding="utf-8")
            self.assertIn("Build and Release APKs", text)

    def test_direct_download_workflow_does_not_mutate_readme(self):
        text = (WORKFLOWS / "update-direct-download-links.yml").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("README.md", text)
        self.assertIn("Morphe-AutoBuilds-Direct-Download.md", text)

    def test_removed_legacy_helpers_stay_removed(self):
        self.assertFalse((ROOT / "scripts" / "download_reused_apks.py").exists())
        self.assertFalse((ROOT / "scripts" / "pr_build_scope.py").exists())

    def test_readme_is_user_facing_and_setup_owns_operations(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        setup = (ROOT / "SETUP.md").read_text(encoding="utf-8")

        self.assertIn("APKをダウンロードする", readme)
        self.assertIn("SETUP.md", readme)
        self.assertNotIn("GPLAY_AAS_TOKEN", readme)
        self.assertNotIn("TS_OAUTH_SECRET", readme)
        self.assertNotIn("version_code` を設定ファイル", readme)

        self.assertIn("必要なSecrets / Variables", setup)
        self.assertIn("Google Play の取得設計", setup)
        self.assertIn("APKダウンロードのフォールバック", setup)
        self.assertIn("probe_apk_sources.py", setup)


if __name__ == "__main__":
    unittest.main()
