import os
import subprocess
import unittest
from unittest import mock

from scripts import download_apks, save_successful_state


class JapanFallbackPolicyTests(unittest.TestCase):
    @mock.patch("scripts.download_apks.shutil.which", return_value=None)
    def test_tailscale_inactive_when_cli_is_absent(self, _which: mock.Mock) -> None:
        self.assertFalse(download_apks._tailscale_fallback_active())

    @mock.patch("scripts.download_apks.subprocess.run")
    @mock.patch("scripts.download_apks.shutil.which", return_value="/usr/bin/tailscale")
    def test_tailscale_active_requires_healthy_daemon(
        self,
        _which: mock.Mock,
        run: mock.Mock,
    ) -> None:
        run.return_value = subprocess.CompletedProcess([], 0)
        self.assertTrue(download_apks._tailscale_fallback_active())
        run.assert_called_once()

    @mock.patch("scripts.download_apks._egress_policy", return_value="japan-first")
    @mock.patch("scripts.download_apks._tailscale_fallback_active", return_value=False)
    def test_japan_first_requests_handoff_before_play(
        self,
        _active: mock.Mock,
        _policy: mock.Mock,
    ) -> None:
        with mock.patch.dict(
            os.environ,
            {"GITHUB_ACTIONS": "true", "APP_NAME": "yuucho-tsucho"},
            clear=False,
        ):
            with self.assertRaisesRegex(RuntimeError, "japan-first"):
                download_apks._request_japan_first_handoff("yuucho-tsucho")

    @mock.patch("scripts.download_apks._egress_policy", return_value="japan-first")
    @mock.patch("scripts.download_apks._tailscale_fallback_active", return_value=True)
    def test_japan_first_continues_when_tailscale_is_active(
        self,
        _active: mock.Mock,
        _policy: mock.Mock,
    ) -> None:
        with mock.patch.dict(
            os.environ,
            {"GITHUB_ACTIONS": "true", "APP_NAME": "yuucho-tsucho"},
            clear=False,
        ):
            download_apks._request_japan_first_handoff("yuucho-tsucho")

    @mock.patch("scripts.download_apks._egress_policy", return_value="japan-first")
    @mock.patch("scripts.download_apks._tailscale_fallback_active")
    def test_library_calls_do_not_require_workflow_tailscale(
        self,
        active: mock.Mock,
        _policy: mock.Mock,
    ) -> None:
        with mock.patch.dict(
            os.environ,
            {"GITHUB_ACTIONS": "", "APP_NAME": ""},
            clear=False,
        ):
            download_apks._request_japan_first_handoff("example")
        active.assert_not_called()

    @mock.patch("scripts.download_apks._tailscale_fallback_active", return_value=True)
    def test_final_provider_rescue_can_retry_through_existing_tailnet(
        self,
        _active: mock.Mock,
    ) -> None:
        with mock.patch.dict(
            os.environ,
            {
                "GITHUB_RUN_ID": "12345",
                "GITHUB_ACTIONS": "",
                "APP_NAME": "yuucho-tsucho",
                "MORPHE_TAILSCALE_PROVIDER_RETRY": "",
            },
            clear=False,
        ):
            self.assertTrue(
                download_apks._final_tailscale_provider_retry_enabled("yuucho-tsucho")
            )

    @mock.patch("scripts.download_apks._tailscale_fallback_active", return_value=True)
    def test_final_provider_rescue_never_loops(
        self,
        _active: mock.Mock,
    ) -> None:
        with mock.patch.dict(
            os.environ,
            {
                "GITHUB_RUN_ID": "12345",
                "GITHUB_ACTIONS": "",
                "APP_NAME": "yuucho-tsucho",
                "MORPHE_TAILSCALE_PROVIDER_RETRY": "1",
            },
            clear=False,
        ):
            self.assertFalse(
                download_apks._final_tailscale_provider_retry_enabled("yuucho-tsucho")
            )

    @mock.patch("scripts.download_apks._tailscale_fallback_active", return_value=True)
    def test_final_provider_rescue_is_not_used_by_normal_actions_stage(
        self,
        _active: mock.Mock,
    ) -> None:
        with mock.patch.dict(
            os.environ,
            {
                "GITHUB_RUN_ID": "12345",
                "GITHUB_ACTIONS": "true",
                "APP_NAME": "yuucho-tsucho",
                "MORPHE_TAILSCALE_PROVIDER_RETRY": "",
            },
            clear=False,
        ):
            self.assertFalse(
                download_apks._final_tailscale_provider_retry_enabled("yuucho-tsucho")
            )


class SuccessfulStateMergeTests(unittest.TestCase):
    def test_concurrent_unrelated_state_is_preserved(self) -> None:
        baseline = {
            "morphe": "v1",
            "youtube-morphe": "20.0",
            "docs-marker": "old",
        }
        desired = {
            "morphe": "v2",
            "youtube-morphe": "20.0",
            "docs-marker": "old",
        }
        fresh = {
            "morphe": "v1",
            "youtube-morphe": "21.0",
            "docs-marker": "new",
            "new-key": "keep-me",
        }

        merged = save_successful_state._merge_concurrent_state(
            baseline,
            desired,
            fresh,
        )

        self.assertEqual(merged["morphe"], "v2")
        self.assertEqual(merged["youtube-morphe"], "21.0")
        self.assertEqual(merged["docs-marker"], "new")
        self.assertEqual(merged["new-key"], "keep-me")

    def test_explicit_deletion_wins_over_fresh_remote(self) -> None:
        baseline = {"obsolete": "yes", "stable": "same"}
        desired = {"stable": "same"}
        fresh = {"obsolete": "newer", "stable": "remote"}

        merged = save_successful_state._merge_concurrent_state(
            baseline,
            desired,
            fresh,
        )

        self.assertNotIn("obsolete", merged)
        self.assertEqual(merged["stable"], "remote")


if __name__ == "__main__":
    unittest.main()
