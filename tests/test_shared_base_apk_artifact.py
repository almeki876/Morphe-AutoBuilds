from __future__ import annotations

import unittest
from pathlib import Path


class SharedBaseApkArtifactTests(unittest.TestCase):
    def test_download_uploads_original_apk_only_once(self) -> None:
        workflow = Path(".github/workflows/build.yml").read_text(encoding="utf-8")
        build_section = workflow.split("  build-apps:\n", 1)[1].split(
            "  report-build-failures:\n", 1
        )[0]

        self.assertIn("name: base-input-${{ matrix.app_name }}-${{ matrix.source }}", build_section)
        self.assertNotIn("base-scan-", build_section)
        self.assertNotIn("base-apk-cache-out/*", build_section)

    def test_build_and_vt_reuse_shared_input_artifact(self) -> None:
        workflow = Path(".github/workflows/build.yml").read_text(encoding="utf-8")
        build_section = workflow.split("  build-apps:\n", 1)[1].split(
            "  report-build-failures:\n", 1
        )[0]
        release_section = workflow.split("  create-release:\n", 1)[1].split(
            "  persist-successful-state:\n", 1
        )[0]

        self.assertIn("python3 -m scripts.scan_virustotal", build_section)
        self.assertIn("--directory base-apk-input", build_section)
        self.assertIn("name: vt-report-${{ matrix.app_name }}-${{ matrix.source }}", build_section)
        self.assertIn("pattern: 'vt-report-*'", release_section)
        self.assertIn("python3 scripts/merge_virustotal_reports.py", release_section)

    def test_cache_promotion_reuses_shared_inputs_after_successful_builds(self) -> None:
        workflow = Path(".github/workflows/build.yml").read_text(encoding="utf-8")
        cache_section = workflow.split("  update-base-apk-cache:\n", 1)[1].split(
            "  create-release:\n", 1
        )[0]

        self.assertIn("needs: [build-apps]", cache_section)
        self.assertIn("pattern: base-input-*", cache_section)
        self.assertIn("pattern: build-status-*", cache_section)
        self.assertIn("python3 -m scripts.stage_shared_base_apk_cache", cache_section)
        self.assertIn("BASE_APK_CACHE_DIR: base-apk-cache-in", cache_section)
        self.assertNotIn("pattern: base-apk-*", cache_section)


if __name__ == "__main__":
    unittest.main()
