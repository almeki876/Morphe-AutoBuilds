from __future__ import annotations

from pathlib import Path


BUILD = Path(".github/workflows/build.yml")
CONFIG = Path(".github/workflows/configuration-check.yml")
VT_TEST = Path("tests/test_virustotal_pipeline_workflow.py")
CLOSER = Path(".github/workflows/close-resolved-build-issues.yml")
SELF = Path("scripts/_migrate_integrated_finalization.py")


def insert_after_once(text: str, anchor: str, payload: str, label: str) -> str:
    if payload.strip() in text:
        return text
    if text.count(anchor) != 1:
        raise SystemExit(f"{label}: expected exactly one anchor, found {text.count(anchor)}")
    return text.replace(anchor, anchor + payload, 1)


def remove_named_step(text: str, name: str) -> str:
    marker = f"      - name: {name}\n"
    start = text.find(marker)
    if start < 0:
        return text
    next_step = text.find("      - name: ", start + len(marker))
    if next_step < 0:
        return text[:start].rstrip() + "\n"
    return text[:start] + text[next_step:]


build = BUILD.read_text(encoding="utf-8")

release_anchor = '''          find ./release-apks -name '*.apk' -print0 \\
            | xargs -0 -r -P 8 -I {} gh release upload "$release_tag" "{}"
'''
vt_upload = '''

          python3 scripts/export_virustotal_cache.py \\
            virustotal_base_results.json \\
            virustotal-cache-v1.json
          test -s virustotal-cache-v1.json
          gh release upload "$release_tag" virustotal-cache-v1.json \\
            --repo "${{ github.repository }}" \\
            --clobber
          echo "VirusTotal SHA cache attached to $release_tag"
'''
if "virustotal-cache-v1.json" not in build.split("  create-release:\n", 1)[1]:
    build = insert_after_once(build, release_anchor, vt_upload, "VirusTotal release cache")

prefix, persist_and_after = build.split("  persist-successful-state:\n", 1)
persist, suffix = persist_and_after.split("  handle-build-failure:\n", 1)
if "      issues: write\n" not in persist:
    permission_anchor = "    permissions:\n      contents: write\n"
    if permission_anchor not in persist:
        raise SystemExit("persist-successful-state permissions anchor not found")
    persist = persist.replace(
        permission_anchor,
        permission_anchor + "      issues: write\n",
        1,
    )

close_step = '''      - name: Close resolved auto-generated build issues
        env:
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
          RUN_URL: ${{ github.server_url }}/${{ github.repository }}/actions/runs/${{ github.run_id }}
        run: python3 scripts/close_resolved_build_issues.py --directory ./build-results

'''
if "close_resolved_build_issues.py" not in persist:
    save_marker = "      - name: Save Resolved Patch and APK Versions\n"
    if save_marker not in persist:
        raise SystemExit("successful-state save step anchor not found")
    persist = persist.replace(save_marker, close_step + save_marker, 1)

build = prefix + "  persist-successful-state:\n" + persist + "  handle-build-failure:\n" + suffix
BUILD.write_text(build, encoding="utf-8")

VT_TEST.write_text(
    '''from __future__ import annotations

import unittest
from pathlib import Path


class VirusTotalPipelineWorkflowTests(unittest.TestCase):
    def test_build_runs_in_parallel_with_virustotal_and_release_waits_for_both(self) -> None:
        workflow = Path(".github/workflows/build.yml").read_text(encoding="utf-8")

        build_marker = "  build-apps:\\n"
        release_marker = "  create-release:\\n"
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
        release_section = workflow.split("  create-release:\\n", 1)[1]
        self.assertIn("virustotal_base_results.json", release_section)
        self.assertIn("export_virustotal_cache.py", release_section)
        self.assertIn("virustotal-cache-v1.json", release_section)
        self.assertIn("gh release upload", release_section)
        self.assertIn("--clobber", release_section)
        self.assertFalse(Path(".github/workflows/publish-virustotal-cache.yml").exists())

    def test_successful_state_job_closes_resolved_issues_in_primary_workflow(self) -> None:
        workflow = Path(".github/workflows/build.yml").read_text(encoding="utf-8")
        persist = workflow.split("  persist-successful-state:\\n", 1)[1].split(
            "  handle-build-failure:\\n", 1
        )[0]
        self.assertIn("issues: write", persist)
        self.assertIn("close_resolved_build_issues.py", persist)
        self.assertFalse(Path(".github/workflows/close-resolved-build-issues.yml").exists())


if __name__ == "__main__":
    unittest.main()
''',
    encoding="utf-8",
)

if CLOSER.exists():
    CLOSER.unlink()

config = CONFIG.read_text(encoding="utf-8")
config = config.replace("  contents: write\n", "  contents: read\n", 1)
config = config.replace("  issues: write\n", "", 1)
for step_name in (
    "Apply one-time integrated finalization migration",
    "Report migration failure",
    "Commit integrated finalization migration",
):
    config = remove_named_step(config, step_name)
CONFIG.write_text(config, encoding="utf-8")

SELF.unlink(missing_ok=True)
print("Integrated VirusTotal publication and successful-build issue cleanup into build.yml.")
