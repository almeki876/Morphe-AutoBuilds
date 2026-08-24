import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import morphe_toolchain_fallback


class MorpheToolchainFallbackTests(unittest.TestCase):
    def test_exact_resource_regression_signature_is_recognized(self) -> None:
        output = (
            "brut.androlib.exceptions.AndrolibException: XmlEncodeException\n"
            "Unexpected array value: <item>@string/morphe_theme_color_custom_entry</item>"
        )
        self.assertTrue(morphe_toolchain_fallback.is_known_resource_regression(output))

    def test_fingerprint_failure_does_not_trigger_toolchain_fallback(self) -> None:
        output = "PatchException: Failed to match the fingerprint"
        self.assertFalse(morphe_toolchain_fallback.is_known_resource_regression(output))
        self.assertFalse(
            morphe_toolchain_fallback.should_retry(
                "morphe", ["python3", "-m", "src"], output
            )
        )

    def test_non_morphe_build_never_uses_morphe_fallback(self) -> None:
        output = "XmlEncodeException\nUnexpected array value"
        self.assertFalse(
            morphe_toolchain_fallback.should_retry(
                "revanced-anddea", ["python3", "-m", "src"], output
            )
        )

    def test_known_good_tags_require_both_cli_and_patch_anchor(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "last-tags.json"
            state.write_text(
                json.dumps(
                    {
                        "morphe_cli": "v1.13.3-dev.1",
                        "morphe": "v1.40.0-dev.21",
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(
                morphe_toolchain_fallback.known_good_tags(state),
                ("v1.13.3-dev.1", "v1.40.0-dev.21"),
            )

            state.write_text(json.dumps({"morphe": "v1.40.0-dev.21"}), encoding="utf-8")
            with self.assertRaises(RuntimeError):
                morphe_toolchain_fallback.known_good_tags(state)

    def test_primary_toolchain_tags_come_from_actual_asset_names(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tools = Path(tmp)
            (tools / "morphe-desktop-1.14.0-dev.1-all.jar").write_bytes(b"jar")
            (tools / "patches-1.40.0-dev.22.mpp").write_bytes(b"mpp")
            (tools / "README.txt").write_text("ignored", encoding="utf-8")
            self.assertEqual(
                morphe_toolchain_fallback.primary_toolchain_tags(tools),
                ("v1.14.0-dev.1", "v1.40.0-dev.22"),
            )

    def test_primary_success_annotation_records_exact_tested_pair(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report_path = root / "build-report.json"
            tools = root / "tools"
            tools.mkdir()
            report_path.write_text(
                json.dumps(
                    {
                        "app_name": "youtube",
                        "source": "morphe",
                        "status": "success",
                        "applied_patches": ["Theme"],
                    }
                ),
                encoding="utf-8",
            )
            (tools / "morphe-desktop-1.14.0-dev.1-all.jar").write_bytes(b"jar")
            (tools / "patches-1.40.0-dev.22.mpp").write_bytes(b"mpp")

            morphe_toolchain_fallback.annotate_primary_success(report_path, tools)
            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertFalse(report["toolchain_fallback_used"])
            self.assertEqual(report["toolchain_primary_cli_tag"], "v1.14.0-dev.1")
            self.assertEqual(report["toolchain_primary_patch_tag"], "v1.40.0-dev.22")

    def test_fallback_can_be_explicitly_disabled(self) -> None:
        output = "XmlEncodeException\nUnexpected array value"
        with mock.patch.dict(os.environ, {"MORPHE_TOOLCHAIN_FALLBACK_DISABLED": "true"}):
            self.assertFalse(
                morphe_toolchain_fallback.should_retry(
                    "morphe", ["python3", "-m", "src"], output
                )
            )

    def test_annotate_build_report_preserves_existing_patch_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            report_path = Path(tmp) / "build-report.json"
            report_path.write_text(
                json.dumps(
                    {
                        "app_name": "youtube-music",
                        "source": "morphe",
                        "status": "success",
                        "applied_patches": ["Theme"],
                    }
                ),
                encoding="utf-8",
            )
            morphe_toolchain_fallback.annotate_build_report(
                {
                    "cli_tag": "v1.13.3-dev.1",
                    "patch_tag": "v1.40.0-dev.21",
                },
                reason="known regression",
                retry_succeeded=True,
                report_path=report_path,
            )
            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(report["applied_patches"], ["Theme"])
            self.assertTrue(report["toolchain_fallback_used"])
            self.assertTrue(report["toolchain_fallback_succeeded"])
            self.assertEqual(report["toolchain_fallback_cli_tag"], "v1.13.3-dev.1")
            self.assertEqual(report["toolchain_fallback_patch_tag"], "v1.40.0-dev.21")


if __name__ == "__main__":
    unittest.main()
