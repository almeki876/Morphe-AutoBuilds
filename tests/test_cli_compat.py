import unittest
from pathlib import Path
from unittest import mock

from src import cli_compat


class CliCompatTests(unittest.TestCase):
    def tearDown(self) -> None:
        cli_compat.clear_probe_cache()

    @mock.patch("src.cli_compat._help_text", return_value="")
    def test_optional_flag_is_omitted_when_help_probe_fails(
        self, help_text: mock.Mock
    ) -> None:
        self.assertFalse(
            cli_compat.supports_flag(Path("morphe-desktop-1.13.1-all.jar"), "patch", "--purge")
        )

    @mock.patch(
        "src.cli_compat._help_text",
        return_value="Usage: patch [--disable-purge] [--continue-on-error]",
    )
    def test_removed_purge_flag_is_not_reported_supported(
        self, help_text: mock.Mock
    ) -> None:
        self.assertFalse(
            cli_compat.supports_flag(Path("morphe-desktop-1.13.1-all.jar"), "patch", "--purge")
        )
        self.assertTrue(
            cli_compat.supports_flag(
                Path("morphe-desktop-1.13.1-all.jar"), "patch", "--disable-purge"
            )
        )


if __name__ == "__main__":
    unittest.main()
