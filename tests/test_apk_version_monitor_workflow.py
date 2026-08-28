from __future__ import annotations

import unittest
from pathlib import Path


class ApkVersionMonitorWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.workflow = Path(".github/workflows/check-upstream.yml").read_text(
            encoding="utf-8"
        )

    def test_patch_pinning_is_resolved_before_latest_apk_monitoring(self) -> None:
        self.assertLess(
            self.workflow.index("- name: Detect version-pinned apps"),
            self.workflow.index("- name: Check APK versions"),
        )
        self.assertIn(
            "VERSION_PINNED_APPS: ${{ steps.pinned.outputs.version_pinned_apps }}",
            self.workflow,
        )

    def test_upstream_monitor_runs_twice_daily_in_japan_time(self) -> None:
        self.assertIn("cron: '0 18 * * *'", self.workflow)
        self.assertIn("cron: '0 12 * * *'", self.workflow)
        self.assertEqual(self.workflow.count("    - cron:"), 2)

    def test_version_monitor_receives_google_play_credentials(self) -> None:
        check_section = self.workflow.split("      - name: Check APK versions\n", 1)[1].split(
            "      - name:", 1
        )[0]
        self.assertIn("GPLAYDL_API_KEY: ${{ secrets.GPLAYDL_API_KEY }}", check_section)
        self.assertIn("GPLAY_EMAIL: ${{ secrets.GPLAY_EMAIL }}", check_section)
        self.assertIn("GPLAY_AAS_TOKEN: ${{ secrets.GPLAY_AAS_TOKEN }}", check_section)
        self.assertIn("GPLAYDL_PREFERRED_PROFILE: ${{ vars.GPLAYDL_PREFERRED_PROFILE }}", check_section)

    def test_japan_egress_for_version_discovery_is_optional(self) -> None:
        self.assertIn("- name: Connect version discovery to Tailscale", self.workflow)
        self.assertIn("id: version-tailscale", self.workflow)
        self.assertIn("- name: Verify Japanese egress for version discovery", self.workflow)

        connect = self.workflow.split(
            "      - name: Connect version discovery to Tailscale\n", 1
        )[1].split("      - name:", 1)[0]
        verify = self.workflow.split(
            "      - name: Verify Japanese egress for version discovery\n", 1
        )[1].split("      - name:", 1)[0]
        self.assertIn("continue-on-error: true", connect)
        self.assertIn("continue-on-error: true", verify)

    def test_full_requirements_are_installed_for_gplaydl_metadata(self) -> None:
        self.assertIn("pip install -r requirements.txt --quiet", self.workflow)

    def test_dispatch_command_ends_without_shell_line_continuation(self) -> None:
        trigger = self.workflow.split(
            "      - name: Trigger build for detected updates\n", 1
        )[1].split("      - name:", 1)[0]
        self.assertIn('-f apk_updated_apps="${APK_UPDATED_APPS:-[]}"', trigger)
        self.assertNotIn('-f apk_updated_apps="${APK_UPDATED_APPS:-[]}" \\\n', trigger)


if __name__ == "__main__":
    unittest.main()
