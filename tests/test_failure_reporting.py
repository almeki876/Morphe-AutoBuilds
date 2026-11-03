import io
import json
import subprocess
import unittest
from contextlib import redirect_stderr
from unittest.mock import patch

from scripts.report_build_failure import (
    _close_related,
    _feature_failures,
    _hypothesis,
    _issue_number,
    _open_issues,
)


class FailureReportingTests(unittest.TestCase):
    def setUp(self) -> None:
        cache = patch("scripts.report_build_failure._OPEN_ISSUES_CACHE", None)
        cache.start()
        self.addCleanup(cache.stop)

    @patch("scripts.report_build_failure.subprocess.run")
    def test_issue_lookup_reuses_a_single_open_issue_snapshot(self, run) -> None:
        run.return_value.stdout = json.dumps(
            [{"number": 123, "title": "[Build Failure] app", "body": "details"}]
        )
        self.assertEqual(len(_open_issues()), 1)
        self.assertEqual(_issue_number("[Build Failure] app"), "123")
        run.assert_called_once()
        self.assertEqual(
            run.call_args.args[0],
            [
                "gh", "issue", "list", "--state", "open", "--limit", "1000",
                "--json", "number,title,body",
            ],
        )

    @patch("scripts.report_build_failure.subprocess.run")
    def test_issue_list_failure_does_not_fail_build_reporting(self, run) -> None:
        run.side_effect = subprocess.CalledProcessError(
            1,
            ["gh", "issue", "list"],
            stderr="GitHub API temporarily unavailable",
        )
        output = io.StringIO()

        with redirect_stderr(output):
            _close_related(
                "[Feature Failure]",
                "youtube",
                "morphe",
                "Resolved by a successful build.",
            )

        self.assertIn("could not list open issues", output.getvalue())
        self.assertIn("skipping automatic issue closure", output.getvalue())
        self.assertIn("GitHub API temporarily unavailable", output.getvalue())
        run.assert_called_once()

    @patch("scripts.report_build_failure.subprocess.run")
    def test_related_issue_closure_uses_cached_body_without_extra_view(self, run) -> None:
        issues = [
            {
                "number": number,
                "title": f"[Feature Failure] youtube - source - v1 - Patch {number}",
                "body": "- **App:** `youtube`\n- **Patch source:** `source`\n",
            }
            for number in (123, 124)
        ]
        run.side_effect = [
            subprocess.CompletedProcess(
                ["gh", "issue", "list"], 0, stdout=json.dumps(issues), stderr=""
            ),
            subprocess.CompletedProcess(
                ["gh", "issue", "close"], 0, stdout="", stderr=""
            ),
            subprocess.CompletedProcess(
                ["gh", "issue", "close"], 0, stdout="", stderr=""
            ),
        ]

        _close_related(
            "[Feature Failure]", "youtube", "source", "Resolved by a successful build."
        )

        self.assertEqual(run.call_count, 3)
        self.assertEqual(
            [call.args[0][3] for call in run.call_args_list[1:]],
            ["123", "124"],
        )

    def test_fingerprint_failure_lists_preparation_alternatives(self) -> None:
        message = _hypothesis(
            "SEVERE: FAILED: Proton VPN Premium\n"
            "PatchException: Failed to match the fingerprint",
            "Patch",
        )
        self.assertIn("wrong APK variant", message)
        self.assertIn("incomplete split bundle", message)
        self.assertNotIn("likely does not match", message)

    def test_missing_native_library_reports_the_actual_input_problem(self) -> None:
        message = _hypothesis(
            "No lib/<abi>/libisvideoengine.so found in the APK.",
            "Patch",
        )
        self.assertIn("required native library is absent", message)
        self.assertIn("merged universal APK", message)

    def test_youtube_aggregate_branding_result_satisfies_formal_option_patches(self) -> None:
        report = {
            "app_name": "youtube",
            "source": "revanced-anddea",
            "applied_patches": ["Custom branding for YouTube"],
            "feature_failures": [
                {"name": "Custom branding icon for YouTube"},
                {"name": "Custom branding name for YouTube"},
                {"name": "Custom header for YouTube"},
            ],
        }
        self.assertEqual(_feature_failures(report), [])

    def test_youtube_music_aggregate_results_satisfy_observed_aliases(self) -> None:
        report = {
            "app_name": "youtube-music",
            "source": "revanced-anddea",
            "applied_patches": ["Custom branding for YouTube Music", "Theme"],
            "feature_failures": [
                {"name": "Custom branding icon for YouTube Music"},
                {"name": "Custom branding name for YouTube Music"},
                {"name": "Custom header for YouTube Music"},
                {"name": "Dark theme"},
            ],
        }
        self.assertEqual(_feature_failures(report), [])

    def test_aggregate_aliases_are_not_applied_to_unrelated_sources(self) -> None:
        failure = {"name": "Custom branding icon for YouTube"}
        report = {
            "app_name": "youtube",
            "source": "another-source",
            "applied_patches": ["Custom branding for YouTube"],
            "feature_failures": [failure],
        }
        self.assertEqual(_feature_failures(report), [failure])

    def test_alias_requires_the_aggregate_patch_to_be_actually_applied(self) -> None:
        failure = {"name": "Dark theme"}
        report = {
            "app_name": "youtube-music",
            "source": "revanced-anddea",
            "applied_patches": [],
            "feature_failures": [failure],
        }
        self.assertEqual(_feature_failures(report), [failure])

    def test_runtime_skipped_outcomes_are_report_only_for_issue_lifecycle(self) -> None:
        report = {
            "app_name": "duolingo",
            "source": "hoo-dles",
            "feature_failures": [
                {"name": "Enable debug mode", "reason": "[runtime-skipped-default] default"},
                {"name": "GmsCore support", "reason": "[runtime-skipped] MicroG"},
            ],
        }
        self.assertEqual(_feature_failures(report), [])

    def test_unsupported_and_failed_outcomes_remain_actionable(self) -> None:
        unsupported = {
            "name": "Example unsupported patch",
            "reason": "[unsupported] not supported in this APK version",
        }
        failed = {
            "name": "Example failed patch",
            "reason": "[failed] CLI reported patch application failure",
        }
        runtime = {
            "name": "Default-only patch",
            "reason": "[runtime-skipped-default] default",
        }
        report = {
            "app_name": "example",
            "source": "example-source",
            "feature_failures": [runtime, unsupported, failed],
        }
        self.assertEqual(_feature_failures(report), [unsupported, failed])


    def test_recommended_unsupported_patch_is_not_actionable(self) -> None:
        recommended = {
            "name": "Recommended upstream patch",
            "reason": "[unsupported] not supported in this APK version",
        }
        explicit = {
            "name": "Explicit patch",
            "reason": "[unsupported] not supported in this APK version",
        }
        report = {
            "app_name": "example",
            "source": "example-source",
            "requested_patches": ["Explicit patch"],
            "feature_failures": [recommended, explicit],
        }
        self.assertEqual(_feature_failures(report), [explicit])

    def test_required_patch_remains_actionable(self) -> None:
        required = {
            "name": "Required patch",
            "reason": "[unsupported] not supported in this APK version",
        }
        report = {
            "app_name": "example",
            "source": "example-source",
            "requested_patches": [],
            "required_patches": ["Required patch"],
            "feature_failures": [required],
        }
        self.assertEqual(_feature_failures(report), [required])


if __name__ == "__main__":
    unittest.main()
