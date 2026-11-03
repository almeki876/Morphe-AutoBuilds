import unittest
from unittest import mock

from src import utils


def _lookup(list_versions_output: str, list_patches_output: str | None = None):
    def fake_run(command, **_kwargs):
        return list_patches_output if "list-patches" in command else list_versions_output

    with mock.patch("src.cli_compat.detect_cli_kind", return_value="morphe"), \
            mock.patch("src.cli_compat.MORPHE", "morphe"), \
            mock.patch.object(utils, "run_process", side_effect=fake_run):
        return utils.get_supported_version_candidates("pkg", "morphe-cli.jar", "p.mpp")


class ListVersionsLogPrefixTests(unittest.TestCase):
    UNIVERSAL = (
        "INFO: Morphe data root: /x (JAR-adjacent)\n"
        "WARN: No compatible versions found in: patches-1.0.3.mpp\n"
    )

    def test_warn_line_is_not_mistaken_for_version_policy(self) -> None:
        # Universal patches: CLI says "No compatible versions" and the bundle
        # does have patches for the package -> unrestricted, latest release.
        self.assertEqual(_lookup(self.UNIVERSAL, "Name: Hide ADB status\n"), [])

    def test_no_compatible_versions_without_any_patch_is_an_error(self) -> None:
        with self.assertRaises(utils.SupportedVersionLookupError):
            _lookup(self.UNIVERSAL, "INFO: nothing\n")

    def test_unverifiable_patch_listing_is_not_assumed_unrestricted(self) -> None:
        with self.assertRaises(utils.SupportedVersionLookupError):
            _lookup(self.UNIVERSAL, None)

    def test_genuinely_unknown_output_still_fails(self) -> None:
        with self.assertRaises(utils.SupportedVersionLookupError):
            _lookup("INFO: x\nsomething unexpected\n")

    def test_explicit_any_policy_is_still_unrestricted(self) -> None:
        self.assertEqual(_lookup("INFO: x\nAny\n"), [])

    def test_log_level_spellings_are_ignored(self) -> None:
        self.assertEqual(_lookup("DEBUG: a\nTRACE: b\nWARNING: c\nany\n"), [])


if __name__ == "__main__":
    unittest.main()
