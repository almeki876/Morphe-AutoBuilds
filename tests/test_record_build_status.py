import tempfile
import unittest
from pathlib import Path

from scripts import record_build_status as status


class RecordBuildStatusTests(unittest.TestCase):
    def test_runtime_patch_outcomes_capture_skips_and_unsupported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "build.log"
            log.write_text(
                "\n".join(
                    [
                        "INFO: Skipping disabled: Default patch (default disabled)",
                        "INFO: Skipping disabled: User choice (disabled by runtime policy)",
                        'WARN: “Old patch” is not supported in this version. Requires newer APK',
                        'WARN: “Old patch” is not supported in this version. Requires newer APK',
                    ]
                ),
                encoding="utf-8",
            )

            self.assertEqual(
                status.runtime_patch_outcomes(log),
                [
                    {
                        "name": "Default patch",
                        "category": "runtime-skipped-default",
                        "reason": "default disabled",
                    },
                    {
                        "name": "User choice",
                        "category": "runtime-skipped",
                        "reason": "disabled by runtime policy",
                    },
                    {
                        "name": "Old patch",
                        "category": "unsupported",
                        "reason": "not supported in this APK version. Requires newer APK",
                    },
                ],
            )

    def test_enrich_report_preserves_exclusions_and_adds_runtime_statuses(self) -> None:
        report = {
            "app_name": "example",
            "source": "source",
            "status": "success",
            "lifecycle_status": "success_partial",
            "applied_patches": ["Applied patch"],
            "feature_failures": [
                {"name": "Configured exclusion", "reason": "disabled by config"}
            ],
            "excluded_patches": [
                {"name": "Upstream exclusion", "reason": "not selected"}
            ],
            "failed_patches": ["Broken patch"],
        }
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "build.log"
            log.write_text(
                "INFO: Skipping disabled: Runtime patch (default disabled)\n"
                'WARN: "Unsupported patch" is not supported in this version.\n',
                encoding="utf-8",
            )
            enriched = status.enrich_report(report, log)

        failures = {
            (item["name"], item["reason"])
            for item in enriched["feature_failures"]
        }
        self.assertIn(("Configured exclusion", "disabled by config"), failures)
        self.assertIn(("Upstream exclusion", "not selected"), failures)
        self.assertIn(
            (
                "Runtime patch",
                "[runtime-skipped-default] default disabled",
            ),
            failures,
        )
        self.assertIn(
            (
                "Unsupported patch",
                "[unsupported] not supported in this APK version",
            ),
            failures,
        )
        self.assertIn(
            ("Broken patch", "[failed] CLI reported patch application failure"),
            failures,
        )
        self.assertEqual(enriched["applied_patches"], ["Applied patch"])
        self.assertEqual(report["feature_failures"], [
            {"name": "Configured exclusion", "reason": "disabled by config"}
        ])


if __name__ == "__main__":
    unittest.main()
