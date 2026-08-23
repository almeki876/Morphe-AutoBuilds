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


class ListVersionsPolicyTests(unittest.TestCase):
    def test_parse_candidates_preserves_unrestricted_policy_with_heading(self):
        candidates = parse_candidates(ISSUE_OUTPUT)

        self.assertEqual(candidates, [])
        self.assertEqual(len(candidates), 0)
        self.assertTrue(candidates)
        self.assertTrue(candidates.unrestricted)

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


if __name__ == "__main__":
    unittest.main()
