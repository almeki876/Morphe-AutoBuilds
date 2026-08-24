import unittest

from scripts.report_build_failure import _feature_failures, _hypothesis


class FailureReportingTests(unittest.TestCase):
    def test_fingerprint_failure_lists_preparation_alternatives(self) -> None:
        message = _hypothesis(
            "SEVERE: FAILED: Proton VPN Premium\n"
            "PatchException: Failed to match the fingerprint",
            "Patch",
        )
        self.assertIn("wrong APK variant", message)
        self.assertIn("incomplete split bundle", message)
        self.assertNotIn("likely does not match", message)

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
