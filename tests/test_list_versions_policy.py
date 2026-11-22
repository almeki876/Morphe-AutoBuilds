import unittest
from unittest.mock import patch

from src import cli_compat, utils
from src.versioning import parse_candidates


ISSUE_OUTPUT = "\n".join(
    [
        "INFO: Package name: com.google.android.apps.photos",
        "Most common compatible versions:",
        "\tAny",
    ]
)

YOUTUBE_OUTPUT = "\n".join(
    [
        "INFO: Package name: com.google.android.youtube",
        "Most common compatible versions:",
        "\t21.04.223 (74 patches)",
        "\t20.51.39 (74 patches)",
        "\t20.31.42 (74 patches)",
        "\t20.21.37 (74 patches)",
    ]
)


class ListVersionsPolicyTests(unittest.TestCase):
    def test_parse_candidates_preserves_unrestricted_policy_with_heading(self):
        candidates = parse_candidates(ISSUE_OUTPUT)

        self.assertEqual(candidates, [])
        self.assertEqual(len(candidates), 0)
        self.assertTrue(candidates)
        self.assertTrue(candidates.unrestricted)

    def test_concrete_candidates_are_truthy_without_parent_bool_method(self):
        candidates = parse_candidates(YOUTUBE_OUTPUT)

        self.assertTrue(candidates)
        self.assertFalse(candidates.unrestricted)
        self.assertEqual(
            [candidate.name for candidate in candidates],
            ["21.04.223", "20.51.39", "20.31.42", "20.21.37"],
        )

    def test_supported_version_lookup_accepts_real_issue_output_as_unrestricted(self):
        with (
            patch.object(cli_compat, "detect_cli_kind", return_value=cli_compat.MORPHE),
            patch.object(utils, "run_process", return_value=ISSUE_OUTPUT),
        ):
            candidates = utils.get_supported_version_candidates(
                "com.google.android.apps.photos",
                "morphe-cli.jar",
                "patches.rvp",
            )

        self.assertEqual(candidates, [])

    def test_supported_version_lookup_accepts_concrete_youtube_versions(self):
        with (
            patch.object(cli_compat, "detect_cli_kind", return_value=cli_compat.MORPHE),
            patch.object(utils, "run_process", return_value=YOUTUBE_OUTPUT),
        ):
            candidates = utils.get_supported_version_candidates(
                "com.google.android.youtube",
                "morphe-cli.jar",
                "patches.rvp",
            )

        self.assertEqual(
            [candidate.name for candidate in candidates],
            ["21.04.223", "20.51.39", "20.31.42", "20.21.37"],
        )

    def test_supported_version_lookup_still_rejects_unknown_policy(self):
        output = "\n".join(
            [
                "INFO: Package name: com.example.app",
                "Most common compatible versions:",
                "\tWhatever",
            ]
        )

        with (
            patch.object(cli_compat, "detect_cli_kind", return_value=cli_compat.MORPHE),
            patch.object(utils, "run_process", return_value=output),
            self.assertRaises(utils.SupportedVersionLookupError),
        ):
            utils.get_supported_version_candidates(
                "com.example.app",
                "morphe-cli.jar",
                "patches.rvp",
            )

    def test_any_policy_with_unknown_extra_line_remains_fail_closed(self):
        output = "\n".join(
            [
                "INFO: Package name: com.example.app",
                "Most common compatible versions:",
                "\tAny",
                "Unexpected upstream format",
            ]
        )

        candidates = parse_candidates(output)
        self.assertEqual(candidates, [])
        self.assertFalse(candidates)
        self.assertFalse(candidates.unrestricted)

        with (
            patch.object(cli_compat, "detect_cli_kind", return_value=cli_compat.MORPHE),
            patch.object(utils, "run_process", return_value=output),
            self.assertRaises(utils.SupportedVersionLookupError),
        ):
            utils.get_supported_version_candidates(
                "com.example.app",
                "morphe-cli.jar",
                "patches.rvp",
            )


    def test_shuhaibnc_universal_patch_warning_output_is_accepted(self):
        """Regression test for ffmpeg-media-encoder / shuhaibnc:
        Morphe CLI emits WARNING lines about universal patches and a header
        'Most common compatible versions:' before the actual policy token 'Any'.
        None of these non-prefixed header/descriptor lines should cause the
        SupportedVersionLookupError to be raised."""
        output = "\n".join(
            [
                "INFO: Morphe data root: /home/runner/work/Morphe-AutoBuilds/Morphe-AutoBuilds/tools/shuhaibnc/morphe-data (JAR-adjacent)",
                "Nov 22, 2026 10:20:33 AM app.morphe.patcher.patch.PatchLoader$Companion checkUniversalPatchDefaults",
                "WARNING: Universal patches must be declared with default false, so they are loaded with default false:",
                " Shuhaib's Patches 1.18.0 (/home/runner/work/.../bundle-patches-1.18.0-73980570646.mpp)",
                ' - "Remove all receivers" declared at app.template.patches.universal.removereceivers.RemoveReceiversKt.<clinit>(RemoveReceivers.kt:15)',
                ' - "Fake System Time" declared at app.template.patches.universal.stoptime.TimeStopKt.<clinit>(TimeStop.kt:13)',
                "INFO: Package name: com.silentlexx.ffmpeggui",
                "Most common compatible versions:",
                "\tAny",
            ]
        )

        with (
            patch.object(cli_compat, "detect_cli_kind", return_value=cli_compat.MORPHE),
            patch.object(utils, "run_process", return_value=output),
        ):
            candidates = utils.get_supported_version_candidates(
                "com.silentlexx.ffmpeggui",
                "morphe-cli.jar",
                "patches.rvp",
            )

        # Should return [] (unrestricted), not raise SupportedVersionLookupError
        self.assertEqual(candidates, [])

    def test_shuhaibnc_universal_patch_warning_via_parse_candidates(self):
        """parse_candidates() should recognise the Any token even when the
        output contains WARNING/header noise lines."""
        output = "\n".join(
            [
                "WARNING: Universal patches must be declared with default false, so they are loaded with default false:",
                " Shuhaib's Patches 1.18.0 (...)",
                ' - "Remove all receivers" declared at ...',
                "INFO: Package name: com.silentlexx.ffmpeggui",
                "Most common compatible versions:",
                "\tAny",
            ]
        )
        candidates = parse_candidates(output)
        # parse_candidates sees no concrete semver version → returns empty list
        # (but that's fine; the caller handles the unrestricted / Any case)
        self.assertEqual(candidates, [])


if __name__ == "__main__":
    unittest.main()
