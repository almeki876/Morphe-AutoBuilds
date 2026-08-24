from __future__ import annotations

import unittest
from pathlib import Path


class SharedBaseApkArtifactTests(unittest.TestCase):
    def test_download_uploads_original_apk_only_once(self) -> None:
        workflow = Path(".github/workflows/build.yml").read_text(encoding="utf-8")
        download_section = workflow.split("  download-apks:\n", 1)[1].split(
            "  scan-base-apks:\n", 1
        )[0]

        self.assertEqual(download_section.count("uses: actions/upload-artifact@v7"), 2)
        self.assertIn("name: base-input-${{ matrix.app_name }}-${{ matrix.source }}", download_section)
        self.assertNotIn("base-scan-", download_section)
        self.assertNotIn("base-apk-cache-out/*", download_section)

    def test_build_and_vt_reuse_shared_input_artifact(self) -> None:
        workflow = Path(".github/workflows/build.yml").read_text(encoding="utf-8")
        scan_section = workflow.split("  scan-base-apks:\n", 1)[1].split(
            "  build-apps:\n", 1
        )[0]
        build_section = workflow.split("  build-apps:\n", 1)[1].split(
            "  report-build-failures:\n", 1
        )[0]

        self.assertIn("pattern: base-input-*", scan_section)
        self.assertNotIn("base-scan-*", scan_section)
        self.assertIn("name: base-input-${{ matrix.app_name }}-${{ matrix.source }}", build_section)
        self.assertNotIn("Upload Verified Base APK Cache Candidate", build_section)

    def test_cache_promotion_reuses_shared_inputs_after_successful_builds(self) -> None:
        workflow = Path(".github/workflows/build.yml").read_text(encoding="utf-8")
        cache_section = workflow.split("  update-base-apk-cache:\n", 1)[1].split(
            "  create-release:\n", 1
        )[0]

        self.assertIn("needs: [download-apks, build-apps]", cache_section)
        self.assertIn("pattern: base-input-*", cache_section)
        self.assertIn("pattern: build-status-*", cache_section)
        self.assertIn("python3 -m scripts.stage_shared_base_apk_cache", cache_section)
        self.assertIn("BASE_APK_CACHE_DIR: base-apk-cache-in", cache_section)
        self.assertNotIn("pattern: base-apk-*", cache_section)


if __name__ == "__main__":
    unittest.main()
