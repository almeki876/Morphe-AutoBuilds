from __future__ import annotations

import unittest
from pathlib import Path


class VirusTotalPipelineWorkflowTests(unittest.TestCase):
    def test_build_runs_in_parallel_with_virustotal_and_release_waits_for_both(self) -> None:
        workflow = Path(".github/workflows/build.yml").read_text(encoding="utf-8")

        build_marker = "  build-apps:\n"
        release_marker = "  create-release:\n"
        build_section = workflow.split(build_marker, 1)[1].split(release_marker, 1)[0]
        release_section = workflow.split(release_marker, 1)[1]

        self.assertIn("needs: [download-tools, prepare-matrix, download-apks]", build_section)
        self.assertNotIn("scan-base-apks", build_section.split("steps:", 1)[0])
        self.assertIn(
            "needs: [download-tools, prepare-matrix, build-apps, scan-base-apks]",
            release_section,
        )
        self.assertIn("needs.scan-base-apks.result == 'success'", release_section)

    def test_success_finalizer_publishes_portable_vt_cache(self) -> None:
        workflow = Path(".github/workflows/close-resolved-build-issues.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("Build and Release APKs", workflow)
        self.assertIn("github.event.workflow_run.conclusion == 'success'", workflow)
        self.assertIn("virustotal-report", workflow)
        self.assertIn("export_virustotal_cache.py", workflow)
        self.assertIn("virustotal-cache-v1.json", workflow)
        self.assertIn("gh release upload", workflow)
        self.assertIn("--clobber", workflow)
        self.assertFalse(Path(".github/workflows/publish-virustotal-cache.yml").exists())


if __name__ == "__main__":
    unittest.main()
