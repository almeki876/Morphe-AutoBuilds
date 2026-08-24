from pathlib import Path


BUILD = Path('.github/workflows/build.yml')
CONFIG = Path('.github/workflows/configuration-check.yml')
VT_TEST = Path('tests/test_virustotal_pipeline_workflow.py')
CLOSER = Path('.github/workflows/close-resolved-build-issues.yml')
SELF = Path('scripts/_migrate_integrated_finalization.py')


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f'{label}: expected exactly one match, found {count}')
    return text.replace(old, new, 1)


build = BUILD.read_text(encoding='utf-8')

release_old = '''          find ./release-apks -name '*.apk' -print0 \\
            | xargs -0 -r -P 8 -I {} gh release upload "$release_tag" "{}"

          echo "Release created and APK assets uploaded successfully!"
'''
release_new = '''          find ./release-apks -name '*.apk' -print0 \\
            | xargs -0 -r -P 8 -I {} gh release upload "$release_tag" "{}"

          python3 scripts/export_virustotal_cache.py \\
            virustotal_base_results.json \\
            virustotal-cache-v1.json
          test -s virustotal-cache-v1.json
          gh release upload "$release_tag" virustotal-cache-v1.json \\
            --repo "${{ github.repository }}" \\
            --clobber
          echo "VirusTotal SHA cache attached to $release_tag"

          echo "Release created and APK assets uploaded successfully!"
'''
build = replace_once(build, release_old, release_new, 'Release upload integration')

prefix, persist_and_after = build.split('  persist-successful-state:\n', 1)
persist, suffix = persist_and_after.split('  handle-build-failure:\n', 1)
persist = replace_once(
    persist,
    '    permissions:\n      contents: write\n',
    '    permissions:\n      contents: write\n      issues: write\n',
    'Persist permissions',
)
close_step = '''      - name: Close resolved auto-generated build issues
        env:
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
          RUN_URL: ${{ github.server_url }}/${{ github.repository }}/actions/runs/${{ github.run_id }}
        run: python3 scripts/close_resolved_build_issues.py --directory ./build-results

'''
persist = replace_once(
    persist,
    '      - name: Save Resolved Patch and APK Versions\n',
    close_step + '      - name: Save Resolved Patch and APK Versions\n',
    'Issue closer integration',
)
build = prefix + '  persist-successful-state:\n' + persist + '  handle-build-failure:\n' + suffix
BUILD.write_text(build, encoding='utf-8')

VT_TEST.write_text('''from __future__ import annotations

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
''', encoding='utf-8')

if CLOSER.exists():
    CLOSER.unlink()

config = CONFIG.read_text(encoding='utf-8')
config = replace_once(
    config,
    '  contents: write\n',
    '  contents: read\n',
    'Restore configuration-check permissions',
)
if '  issues: write\n' in config:
    config = config.replace('  issues: write\n', '', 1)
migration_step = '''      - name: Apply one-time integrated finalization migration
        run: python3 scripts/_migrate_integrated_finalization.py

'''
config = replace_once(config, migration_step, '', 'Remove one-time migration step')
commit_step = '''      - name: Commit integrated finalization migration
        run: |
          git config user.name "github-actions[bot]"
          git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
          git add -A
          git commit -m "ci: integrate successful-build finalization into primary workflow [skip ci]"
          git pull --rebase origin main
          git push origin HEAD:main

'''
config = replace_once(config, commit_step, '', 'Remove one-time migration commit step')
diagnostic_step = '''      - name: Report migration failure
        if: failure()
        env:
          GH_TOKEN: ${{ github.token }}
        run: |
          body="Migration failed in run ${{ github.server_url }}/${{ github.repository }}/actions/runs/${{ github.run_id }}"
          existing=$(gh issue list --state open --search 'TEMP integrated finalization migration failure in:title' --json number,title --jq '.[] | select(.title == "TEMP integrated finalization migration failure") | .number' | head -n 1)
          if [ -n "$existing" ]; then
            gh issue comment "$existing" --body "$body"
          else
            gh issue create --title 'TEMP integrated finalization migration failure' --body "$body"
          fi

'''
if diagnostic_step in config:
    config = config.replace(diagnostic_step, '', 1)
CONFIG.write_text(config, encoding='utf-8')

SELF.unlink()
print('Integrated VirusTotal publication and resolved-issue cleanup into build.yml.')
