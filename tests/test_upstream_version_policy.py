import unittest
from pathlib import Path
from unittest.mock import patch

from src import upstream_policy


class UpstreamVersionPolicyTests(unittest.TestCase):
    def test_explicit_any_means_no_version_restriction(self) -> None:
        with (
            patch.object(
                upstream_policy,
                "_tool_files",
                return_value=(Path("cli.jar"), Path("bundle.mpp")),
            ),
            patch(
                "src.utils.get_supported_version_candidates",
                return_value=[],
            ),
            patch.object(
                upstream_policy,
                "_list_versions_output",
                return_value="INFO: Package name: com.example.app\nany\n",
            ),
        ):
            self.assertFalse(
                upstream_policy._patch_has_version_restriction(
                    "com.example.app",
                    "source",
                )
            )

    def test_lookup_failure_is_not_treated_as_any(self) -> None:
        with (
            patch.object(
                upstream_policy,
                "_tool_files",
                return_value=(Path("cli.jar"), Path("bundle.mpp")),
            ),
            patch(
                "src.utils.get_supported_version_candidates",
                return_value=[],
            ),
            patch.object(
                upstream_policy,
                "_list_versions_output",
                return_value="ERROR: failed to list versions\n",
            ),
        ):
            self.assertIsNone(
                upstream_policy._patch_has_version_restriction(
                    "com.example.app",
                    "source",
                )
            )

    def test_explicit_null_means_no_version_restriction(self) -> None:
        with (
            patch.object(
                upstream_policy,
                "_tool_files",
                return_value=(Path("cli.jar"), Path("bundle.mpp")),
            ),
            patch(
                "src.utils.get_supported_version_candidates",
                return_value=[],
            ),
            patch.object(
                upstream_policy,
                "_list_versions_output",
                return_value="INFO: Package name: com.example.app\nnull\n",
            ),
        ):
            self.assertFalse(
                upstream_policy._patch_has_version_restriction(
                    "com.example.app",
                    "source",
                )
            )

    def test_explicit_versions_are_restricted(self) -> None:
        candidate = object()
        with (
            patch.object(
                upstream_policy,
                "_tool_files",
                return_value=(Path("cli.jar"), Path("bundle.mpp")),
            ),
            patch(
                "src.utils.get_supported_version_candidates",
                return_value=[candidate],
            ),
        ):
            self.assertTrue(
                upstream_policy._patch_has_version_restriction(
                    "com.example.app",
                    "source",
                )
            )


if __name__ == "__main__":
    unittest.main()
