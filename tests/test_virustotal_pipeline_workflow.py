from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path


class VirusTotalPipelineWorkflowTests(unittest.TestCase):
    def test_build_runs_in_parallel_with_virustotal_and_release_waits_for_both(self) -> None:
        workflow = Path(".github/workflows/build.yml").read_text(encoding="utf-8")

        build_marker = "  build-apps:\n"
        release_marker = "  create-release:\n"
        build_section = workflow.split(build_marker, 1)[1].split(release_marker, 1)[0]
        release_section = workflow.split(release_marker, 1)[1]

        self.assertIn(
            "needs: [download-tools, prepare-matrix, prepare-release-tag]",
            build_section,
        )
        self.assertNotIn("scan-base-apks", build_section.split("steps:", 1)[0])
        self.assertIn("Start Concurrent VirusTotal Base APK Scan", build_section)
        self.assertIn("Wait for Concurrent VirusTotal Scan", build_section)
        self.assertIn(
            "needs: [download-tools, prepare-matrix, prepare-release-tag, build-apps]",
            release_section,
        )
        self.assertIn("needs.build-apps.result != 'skipped'", release_section)
        self.assertIn("Consolidate VirusTotal Reports", release_section)

    def test_create_release_attaches_portable_vt_cache(self) -> None:
        workflow = Path(".github/workflows/build.yml").read_text(encoding="utf-8")
        release_section = workflow.split("  create-release:\n", 1)[1]
        release_script = Path("scripts/create_release.py").read_text(encoding="utf-8")

        self.assertIn("python3 -m scripts.create_release", release_section)
        self.assertIn("RELEASE_TAG", release_section)
        self.assertIn("GITHUB_REPOSITORY", release_section)
        self.assertIn("export_cache", release_script)
        self.assertIn("virustotal_base_results.json", release_script)
        self.assertIn("virustotal-cache-v1.json", release_script)
        self.assertIn('"--clobber"', release_script)
        self.assertFalse(Path(".github/workflows/publish-virustotal-cache.yml").exists())

    def test_merge_reports_runs_without_installed_dependencies(self) -> None:
        """Regression test for run 45084059347.

        The Create Integrated Release job sets up Python with
        ``install-dependencies: 'false'``, so ``curl-cffi`` is not
        installed there. ``merge_virustotal_reports.py`` used to import
        ``ScanResult``/``markdown_report`` from ``scripts.virustotal``,
        whose top-level ``curl_cffi`` import made the consolidate step
        fail with ``ModuleNotFoundError: No module named 'curl_cffi'``.
        The report types now live in the standard-library-only
        ``scripts.virustotal_report`` module, so the merge must run even
        when ``curl_cffi`` cannot be imported at all.
        """
        root = Path(__file__).resolve().parents[1]
        blocker = textwrap.dedent(
            """
            import sys


            class _BlockCurlCffi:
                def find_module(self, name, path=None):
                    if name == "curl_cffi" or name.startswith("curl_cffi."):
                        return self
                    return None

                def load_module(self, name):
                    raise ImportError(f"blocked in test: {name}")


            sys.meta_path.insert(0, _BlockCurlCffi())
            """
        )
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            sitecustomize_dir = tmp_path / "blocker"
            sitecustomize_dir.mkdir()
            (sitecustomize_dir / "sitecustomize.py").write_text(
                blocker, encoding="utf-8"
            )
            empty_dir = tmp_path / "vt-chunks"
            empty_dir.mkdir()
            env = dict(os.environ)
            env["PYTHONPATH"] = os.pathsep.join(
                [str(sitecustomize_dir), str(root)]
            )
            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/merge_virustotal_reports.py",
                    "--directory",
                    str(empty_dir),
                    "--output-json",
                    str(tmp_path / "out.json"),
                    "--output-markdown",
                    str(tmp_path / "out.md"),
                ],
                cwd=root,
                env=env,
                capture_output=True,
                text=True,
                timeout=60,
            )
            self.assertEqual(
                result.returncode,
                0,
                f"merge failed without curl_cffi: {result.stderr}",
            )
            self.assertIn("skipping consolidated report", result.stdout)

    def test_successful_state_job_closes_resolved_issues_in_primary_workflow(self) -> None:
        workflow = Path(".github/workflows/build.yml").read_text(encoding="utf-8")
        persist = workflow.split("  persist-successful-state:\n", 1)[1].split(
            "  handle-build-failure:\n", 1
        )[0]
        self.assertIn("issues: write", persist)
        self.assertIn("close_resolved_build_issues.py", persist)
        self.assertFalse(Path(".github/workflows/close-resolved-build-issues.yml").exists())


if __name__ == "__main__":
    unittest.main()
