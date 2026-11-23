"""Tests for auto-closing [Source Unavailable] issues after source recovery.

Regression context: issue #1554 (rushiranpise) stayed open after the source
recovered in a full build whose download-tools job hit the tools cache, so
scripts/report_tool_download_failures.py never saw a download status file.
The persist-successful-state cleanup (close_resolved_build_issues.py) is the
path that observes recovery from per-app build reports, so it must also
cover [Source Unavailable] issues.
"""

from __future__ import annotations

import subprocess
import unittest
from pathlib import Path
from unittest import mock

from scripts import close_resolved_build_issues as cleanup


def _success_report(app: str = "adguard", source: str = "rushiranpise") -> dict:
    return {
        "app_name": app,
        "source": source,
        "source_name": source,
        "status": "success",
        "failed_patches": [],
        "required_failures": [],
        "required_patches_satisfied": True,
    }


def _source_issue(number: int, source: str = "rushiranpise") -> dict:
    return {
        "number": number,
        "title": f"[Source Unavailable] Build tools failed to download for {source}",
        "body": f"# Upstream Source Unavailable: `{source}`\n",
    }


class SourceUnavailableCleanupTests(unittest.TestCase):
    def _run_cleanup(self, reports: list[dict], issues: list[dict]) -> tuple[int, list[list[str]]]:
        commands: list[list[str]] = []

        def fake_run(args, **_kwargs):
            commands.append(list(args))
            return subprocess.CompletedProcess(args, 0)

        with (
            mock.patch.object(cleanup, "_load_reports", lambda _root: reports),
            mock.patch.object(cleanup, "_open_auto_issues", lambda: issues),
            mock.patch.object(cleanup.subprocess, "run", fake_run),
        ):
            closed = cleanup.close_resolved(Path("."), "https://example.invalid/run/1")
        return closed, commands

    def test_closes_source_unavailable_issue_after_successful_build(self) -> None:
        closed, commands = self._run_cleanup([_success_report()], [_source_issue(1554)])
        self.assertEqual(closed, 1)
        close_cmds = [c for c in commands if c[:3] == ["gh", "issue", "close"]]
        self.assertEqual(len(close_cmds), 1)
        self.assertEqual(close_cmds[0][3], "1554")
        comment = close_cmds[0][close_cmds[0].index("--comment") + 1]
        self.assertIn("rushiranpise", comment)
        self.assertIn("tool download failure", comment)

    def test_does_not_close_when_source_build_failed(self) -> None:
        report = _success_report()
        report["status"] = "failure"
        closed, commands = self._run_cleanup([report], [_source_issue(1554)])
        self.assertEqual(closed, 0)
        self.assertEqual([c for c in commands if c[:3] == ["gh", "issue", "close"]], [])

    def test_does_not_close_on_other_source_success(self) -> None:
        report = _success_report(app="youtube", source="morphe")
        closed, _commands = self._run_cleanup([report], [_source_issue(1554)])
        self.assertEqual(closed, 0)

    def test_universal_alias_success_closes_rushiranpise_issue(self) -> None:
        report = _success_report(app="yuucho-tsucho", source="rushiranpise-universal")
        closed, commands = self._run_cleanup([report], [_source_issue(1554)])
        self.assertEqual(closed, 1)
        close_cmds = [c for c in commands if c[:3] == ["gh", "issue", "close"]]
        self.assertEqual(close_cmds[0][3], "1554")

    def test_closes_all_open_issues_for_same_source(self) -> None:
        issues = [_source_issue(1554), _source_issue(1600)]
        closed, commands = self._run_cleanup([_success_report()], issues)
        self.assertEqual(closed, 2)
        closed_numbers = sorted(
            c[3] for c in commands if c[:3] == ["gh", "issue", "close"]
        )
        self.assertEqual(closed_numbers, ["1554", "1600"])

    def test_matches_requires_exact_title_format(self) -> None:
        report = _success_report()
        good = _source_issue(1554)
        self.assertTrue(cleanup._matches(good, report))
        malformed = {
            "number": 42,
            "title": "[Source Unavailable] something else mentioning rushiranpise",
            "body": "`adguard` `rushiranpise`",
        }
        self.assertFalse(cleanup._matches(malformed, report))
        other_source = _source_issue(43, source="hoo-dles")
        self.assertFalse(cleanup._matches(other_source, report))


if __name__ == "__main__":
    unittest.main()
