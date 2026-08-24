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

    @mock.patch("scripts.download_apks._tailscale_fallback_active", return_value=False)
    def test_play_failure_requests_japan_before_mirrors(self, _active: mock.Mock) -> None:
        original = RuntimeError("Google Play unavailable")
        with mock.patch.dict(
            os.environ,
            {"GITHUB_ACTIONS": "true", "APP_NAME": "yuucho-tsucho"},
            clear=False,
        ):
            with self.assertRaisesRegex(RuntimeError, "Japanese egress fallback"):
                download_apks._require_japan_fallback_before_providers(
                    "yuucho-tsucho",
                    original,
                )

    @mock.patch("scripts.download_apks._tailscale_fallback_active", return_value=True)
    def test_play_failure_can_continue_to_mirrors_after_japan_retry(
        self,
        _active: mock.Mock,
    ) -> None:
        with mock.patch.dict(
            os.environ,
            {"GITHUB_ACTIONS": "true", "APP_NAME": "yuucho-tsucho"},
            clear=False,
        ):
            download_apks._require_japan_fallback_before_providers(
                "yuucho-tsucho",
                RuntimeError("Google Play unavailable"),
            )

    @mock.patch("scripts.download_apks._tailscale_fallback_active")
    def test_library_calls_do_not_require_workflow_tailscale(
        self,
        active: mock.Mock,
    ) -> None:
        with mock.patch.dict(
            os.environ,
            {"GITHUB_ACTIONS": "", "APP_NAME": ""},
            clear=False,
        ):
            download_apks._require_japan_fallback_before_providers(
                "example",
                RuntimeError("Google Play unavailable"),
            )
        active.assert_not_called()


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
