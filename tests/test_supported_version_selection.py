import unittest
from unittest import mock

from src import cli_compat, utils


class SupportedVersionSelectionTests(unittest.TestCase):
    def _resolve(self, output: str | None):
        with (
            mock.patch.object(
                cli_compat,
                "detect_cli_kind",
                return_value=cli_compat.MORPHE,
            ),
            mock.patch.object(utils, "run_process", return_value=output),
        ):
            return utils.get_supported_version_candidates(
                "com.example.app", "cli.jar", "patches.mpp"
            )

    def test_any_selects_latest(self) -> None:
        self.assertEqual(self._resolve("INFO: package selected\nany"), [])

    def test_null_selects_latest(self) -> None:
        self.assertEqual(self._resolve("INFO: package selected\nnull"), [])

    def test_successful_info_only_output_selects_latest(self) -> None:
        self.assertEqual(self._resolve("INFO: package has no version constraint"), [])

    def test_explicit_versions_are_sorted_and_preserved(self) -> None:
        candidates = self._resolve("1.2.3 (2 patches)\n1.3.0 (1 patch)")
        self.assertEqual(
            [candidate.name for candidate in candidates],
            ["1.3.0", "1.2.3"],
        )

    def test_empty_output_is_not_silently_treated_as_latest(self) -> None:
        with self.assertRaises(utils.SupportedVersionLookupError):
            self._resolve("")

    def test_cli_error_is_not_silently_treated_as_latest(self) -> None:
        with self.assertRaises(utils.SupportedVersionLookupError):
            self._resolve("INFO: starting\nERROR: failed to read patch bundle")

    def test_unknown_output_is_not_silently_treated_as_latest(self) -> None:
        with self.assertRaises(utils.SupportedVersionLookupError):
            self._resolve("unexpected response")


if __name__ == "__main__":
    unittest.main()
