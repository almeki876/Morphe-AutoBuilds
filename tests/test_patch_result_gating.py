import json
import os
import tempfile
import unittest
from pathlib import Path

from src.__main__ import PatchConfig, PatchFailureParser, _write_build_report


class PatchResultGatingTests(unittest.TestCase):
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
