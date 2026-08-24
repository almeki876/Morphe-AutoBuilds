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

    def test_create_release_attaches_portable_vt_cache(self) -> None:
        workflow = Path(".github/workflows/build.yml").read_text(encoding="utf-8")
        release_section = workflow.split("  create-release:\n", 1)[1]
        self.assertIn("virustotal_base_results.json", release_section)
        self.assertIn("export_virustotal_cache.py", release_section)
        self.assertIn("virustotal-cache-v1.json", release_section)
        self.assertIn("gh release upload", release_section)
        self.assertIn("--clobber", release_section)
        self.assertFalse(Path(".github/workflows/publish-virustotal-cache.yml").exists())

    def test_issue_closer_does_not_duplicate_vt_publication(self) -> None:
        closer = Path(".github/workflows/close-resolved-build-issues.yml").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("virustotal-cache-v1.json", closer)
        self.assertNotIn("export_virustotal_cache.py", closer)


if __name__ == "__main__":
    unittest.main()
