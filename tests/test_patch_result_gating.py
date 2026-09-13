import json
import os
import tempfile
import unittest
from pathlib import Path

from src.__main__ import (
    PatchConfig,
    PatchFailureParser,
    PatchOption,
    _build_patch_flags,
    _semantic_patch_failure,
    _selected_patch_options,
    _write_build_report,
)


class PatchResultGatingTests(unittest.TestCase):
    def test_patch_events_do_not_depend_on_localized_log_level(self) -> None:
        parser = PatchFailureParser()
        parser("情報: Applying 2 patches...\n")
        parser("情報: Applied: Working patch\n")
        parser("重大: FAILED: Broken patch\n")

        self.assertEqual(parser.applying_count, 2)
        self.assertEqual(parser.applied_result(), ["Working patch"])
        self.assertEqual(parser.result(), ["Broken patch"])

    def test_option_does_not_enable_non_default_patch(self) -> None:
        config = PatchConfig(
            app_name="youtube",
            source="revanced-anddea",
            options=[PatchOption("Option only", "color", "blue")],
        )
        with tempfile.TemporaryDirectory() as directory:
            tools_dir = Path(directory)
            source_dir = tools_dir / "revanced-anddea"
            source_dir.mkdir()
            (source_dir / "patches-list.json").write_text(
                json.dumps(
                    {
                        "patches": [
                            {
                                "name": "Default patch",
                                "use": True,
                                "compatiblePackages": [
                                    {"packageName": "com.google.android.youtube"}
                                ],
                            },
                            {
                                "name": "Option only",
                                "use": False,
                                "compatiblePackages": [
                                    {"packageName": "com.google.android.youtube"}
                                ],
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )
            enables, _ = _build_patch_flags(
                "youtube", "revanced-anddea", "v5plus", config, tools_dir
            )

        self.assertEqual(enables, ["-e", "Default patch"])
        self.assertEqual(_selected_patch_options(config.options, enables), [])

    def test_force_enable_still_selects_non_default_patch_and_its_options(self) -> None:
        option = PatchOption("Forced patch", "name", "value")
        config = PatchConfig(
            app_name="youtube",
            source="revanced-anddea",
            options=[option],
            force_enable=["Forced patch"],
        )
        with tempfile.TemporaryDirectory() as directory:
            tools_dir = Path(directory)
            source_dir = tools_dir / "revanced-anddea"
            source_dir.mkdir()
            (source_dir / "patches-list.json").write_text(
                json.dumps(
                    {
                        "patches": [
                            {
                                "name": "Forced patch",
                                "use": False,
                                "compatiblePackages": [
                                    {"packageName": "com.google.android.youtube"}
                                ],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            enables, _ = _build_patch_flags(
                "youtube", "revanced-anddea", "v5plus", config, tools_dir
            )

        self.assertEqual(enables, ["-e", "Forced patch"])
        self.assertEqual(_selected_patch_options(config.options, enables), [option])

    def test_option_only_patch_is_not_a_requested_feature(self) -> None:
        config = PatchConfig(
            app_name="example",
            source="source",
            options=[PatchOption("Optional patch", "mode", "custom")],
        )
        old_cwd = Path.cwd()
        with tempfile.TemporaryDirectory() as directory:
            os.chdir(directory)
            try:
                _write_build_report(
                    "example",
                    "source",
                    "1.0",
                    "source",
                    ["-e", "Default patch"],
                    [],
                    config,
                    "success",
                    applied_patches=["Default patch"],
                    applying_count=1,
                )
                report = json.loads(
                    Path("build-metadata/build-report.json").read_text(encoding="utf-8")
                )
            finally:
                os.chdir(old_cwd)

        self.assertEqual(report["requested_patches"], [])
        self.assertEqual(report["feature_failures"], [])
        self.assertEqual(report["lifecycle_status"], "success_full")
        self.assertTrue(report["fully_applied"])

    def test_requested_eight_applying_zero_is_partial(self) -> None:
        parser = PatchFailureParser()
        parser("INFO: Applying 0 patches...\n")
        requested = [f"Patch {index}" for index in range(8)]
        report = self._report(requested, parser)
        self.assertEqual(report["applying_count"], 0)
        self.assertEqual(report["applied_patches"], [])
        self.assertEqual(len(report["feature_failures"]), 8)
        self.assertEqual(report["lifecycle_status"], "success_partial")
        self.assertFalse(report["fully_applied"])

    def test_requested_seven_applied_five_records_two_missing(self) -> None:
        parser = PatchFailureParser()
        parser("INFO: Applying 5 patches...\n")
        requested = [f"Patch {index}" for index in range(7)]
        for name in requested[:5]:
            parser(f"INFO: Applied: {name}\n")
        report = self._report(requested, parser)
        self.assertEqual(report["applied_patches"], requested[:5])
        self.assertEqual(
            [item["name"] for item in report["feature_failures"]],
            requested[5:],
        )
        self.assertEqual(report["lifecycle_status"], "success_partial")

    def test_required_patch_missing_from_actual_applied_is_failure(self) -> None:
        parser = PatchFailureParser()
        parser("INFO: Applying 1 patches...\n")
        parser("INFO: Applied: Optional Patch\n")
        required = ["Required Patch"]
        self.assertEqual(
            [name for name in required if name not in parser.applied_result()],
            required,
        )

    def test_required_patch_present_in_actual_applied_is_satisfied(self) -> None:
        parser = PatchFailureParser()
        parser("INFO: Applying 1 patches...\n")
        parser("INFO: Applied: Required Patch\n")
        required = ["Required Patch"]
        self.assertEqual(
            [name for name in required if name not in parser.applied_result()],
            [],
        )

    def test_failed_patch_parsing_is_preserved(self) -> None:
        parser = PatchFailureParser()
        parser("SEVERE: FAILED: Broken Patch\n")
        self.assertEqual(parser.result(), ["Broken Patch"])

    def test_run_build_rejects_any_cli_reported_patch_failure(self) -> None:
        failure = _semantic_patch_failure(
            ["Broken Patch"], [], ["Working Patch"], 2
        )
        self.assertIsNotNone(failure)
        self.assertEqual(failure[0], "PATCH_APPLY_FAILED")
        self.assertIn("Broken Patch", failure[1])

    def test_run_build_rejects_applied_count_mismatch(self) -> None:
        failure = _semantic_patch_failure([], [], ["Only Patch"], 2)
        self.assertIsNotNone(failure)
        self.assertEqual(failure[0], "PATCH_APPLY_FAILED")
        self.assertIn("announced 2 patch(es)", failure[1])

    def _report(self, requested: list[str], parser: PatchFailureParser) -> dict:
        config = PatchConfig(
            app_name="example",
            source="source",
            force_enable=requested,
        )
        enables = []
        for name in requested:
            enables.extend(["-e", name])
        old_cwd = Path.cwd()
        with tempfile.TemporaryDirectory() as directory:
            os.chdir(directory)
            try:
                _write_build_report(
                    "example",
                    "source",
                    "1.0",
                    "source",
                    enables,
                    [],
                    config,
                    "success",
                    failed_patches=parser.result(),
                    applied_patches=parser.applied_result(),
                    applying_count=parser.applying_count,
                )
                return json.loads(
                    Path("build-metadata/build-report.json").read_text(encoding="utf-8")
                )
            finally:
                os.chdir(old_cwd)


if __name__ == "__main__":
    unittest.main()
