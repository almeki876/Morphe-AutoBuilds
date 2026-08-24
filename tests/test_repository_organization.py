import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"


class RepositoryOrganizationTests(unittest.TestCase):
    IMPORTANT_WORKFLOW_NAMES = {
        "build.yml": "Build and Release APKs",
        "build-all-apps.yml": "手動: 全アプリをビルド",
        "check-upstream.yml": "自動: アップストリーム更新を確認",
        "configuration-check.yml": "CI: 設定・テスト検証",
        "diagnose-google-play-purchase.yml": "保守: Google Play取得を診断",
        "health-check.yml": "保守: 取得元とビルド環境を点検",
        "japan-egress-check.yml": "保守: 日本Tailscale経路を確認",
        "publish-release-details.yml": "保守: Release詳細を再生成",
        "register-google-play.yml": "セットアップ: Google Playアカウントを登録",
        "update-direct-download-links.yml": "保守: APK直リンク一覧を再生成",
    }

    def _workflow_name(self, path: Path) -> str:
        first_line = path.read_text(encoding="utf-8").splitlines()[0]
        match = re.fullmatch(r'name:\s*["\']?(.*?)["\']?', first_line)
        self.assertIsNotNone(match, f"workflow has no readable name: {path}")
        return match.group(1)

    def test_important_workflows_have_stable_human_readable_names(self):
        for filename, expected in self.IMPORTANT_WORKFLOW_NAMES.items():
            path = WORKFLOWS / filename
            self.assertTrue(path.exists(), f"required workflow missing: {filename}")
            self.assertEqual(self._workflow_name(path), expected)

    def test_manual_full_build_keeps_a_simple_entry_point(self):
        text = (WORKFLOWS / "build-all-apps.yml").read_text(encoding="utf-8")
        self.assertIn("workflow_dispatch:", text)
        self.assertIn("gh workflow run build.yml", text)
        self.assertIn("build_all_sources=true", text)

    def test_recovery_workflows_are_manual_only(self):
        for filename in (
            "publish-release-details.yml",
            "update-direct-download-links.yml",
            "diagnose-google-play-purchase.yml",
            "japan-egress-check.yml",
            "register-google-play.yml",
        ):
            text = (WORKFLOWS / filename).read_text(encoding="utf-8")
            self.assertIn("workflow_dispatch:", text)
            self.assertNotIn("workflow_run:", text)

    def test_direct_download_workflow_is_manual_recovery_only(self):
        text = (WORKFLOWS / "update-direct-download-links.yml").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("push:", text)
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
