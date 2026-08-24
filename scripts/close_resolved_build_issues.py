"""Close stale auto-generated build issues after a successful workflow run."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from scripts.report_build_failure import _feature_failures

AUTO_PREFIXES = (
    "[Build Failure]",
    "[Feature Failure]",
    "[Partial Patch Failure]",
)
GITHUB_ACTIONS_BOT_LOGINS = {"github-actions", "github-actions[bot]"}
FEATURE_NAME_RE = re.compile(r"^- \*\*Failed patch feature:\*\* `(.+?)`$", re.MULTILINE)


def _load_reports(root: Path) -> list[dict]:
    reports: list[dict] = []
    for path in sorted(root.rglob("*.json")):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(value, dict) and value.get("app_name") and value.get("source"):
            reports.append(value)
    return reports


def _build_succeeded(report: dict) -> bool:
    """Return True when this app/source completed without build-level failures."""
    return (
        report.get("status") == "success"
        and not (report.get("failed_patches") or [])
        and not (report.get("required_failures") or [])
        and report.get("required_patches_satisfied", True) is not False
    )


def _open_auto_issues() -> list[dict]:
    result = subprocess.run(
        [
            "gh", "issue", "list",
            "--state", "open",
            "--limit", "500",
            "--json", "number,title,body,author",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    value = json.loads(result.stdout or "[]")
    if not isinstance(value, list):
        return []
    return [
        issue for issue in value
        if isinstance(issue, dict)
        and str(issue.get("title") or "").startswith(AUTO_PREFIXES)
        and str((issue.get("author") or {}).get("login") or "") in GITHUB_ACTIONS_BOT_LOGINS
    ]


def _matches(issue: dict, report: dict) -> bool:
    title = str(issue.get("title") or "")
    body = str(issue.get("body") or "")
    app = str(report.get("app_name") or "")
    source = str(report.get("source") or "")
    source_name = str(report.get("source_name") or report.get("patch_source") or source)

    if title.startswith("[Build Failure]"):
        return title.startswith(f"[Build Failure] {app} - {source} -")
    if title.startswith("[Feature Failure]"):
        return title.startswith(f"[Feature Failure] {app} - {source_name} -")
    if title.startswith("[Partial Patch Failure]"):
        return title.startswith(f"[Partial Patch Failure] {app} ({source_name}) -")

    # Defensive fallback for older auto-generated title formats.
    return (
        f"`{app}`" in body
        and (
            f"`{source_name}`" in body
            or f"{app} (" in body
        )
    )


def _feature_issue_name(issue: dict) -> str:
    """Return the exact tracked feature name for a Feature Failure issue."""
    body = str(issue.get("body") or "")
    match = FEATURE_NAME_RE.search(body)
    if match:
        return match.group(1).strip()

    title = str(issue.get("title") or "")
    if title.startswith("[Feature Failure]") and " - " in title:
        return title.rsplit(" - ", 1)[-1].strip()
    return ""


def _issue_resolved(issue: dict, report: dict) -> bool:
    """Return whether this specific tracking issue is resolved by the new report.

    Feature issues are evaluated individually. A successful app/source can keep
    a real unsupported/failed feature open while stale [runtime-skipped*]
    report-only issues for other features are closed.
    """
    if not _build_succeeded(report):
        return False

    title = str(issue.get("title") or "")
    actionable = {
        str(failure.get("name") or "").strip()
        for failure in _feature_failures(report)
        if isinstance(failure, dict) and str(failure.get("name") or "").strip()
    }

    if title.startswith("[Build Failure]"):
        return True
    if title.startswith("[Feature Failure]"):
        feature = _feature_issue_name(issue)
        return bool(feature) and feature not in actionable
    if title.startswith("[Partial Patch Failure]"):
        return not actionable
    return False


def close_resolved(root: Path, run_url: str) -> int:
    successful = [report for report in _load_reports(root) if _build_succeeded(report)]
    if not successful:
        print("No successful app/source reports; no issues will be closed.")
        return 0

    issues = _open_auto_issues()
    closed: set[int] = set()
    for report in successful:
        app = str(report.get("app_name"))
        source = str(report.get("source_name") or report.get("source"))
        for issue in issues:
            number = int(issue.get("number") or 0)
            if (
                not number
                or number in closed
                or not _matches(issue, report)
                or not _issue_resolved(issue, report)
            ):
                continue
            message = (
                f"CI verified a later successful build for `{app}` with patch source `{source}`, "
                "and the condition tracked by this auto-generated issue is no longer an "
                "actionable failure. Closing it as resolved."
            )
            if run_url:
                message += f"\n\nSuccessful workflow run: {run_url}"
            subprocess.run(
                [
                    "gh", "issue", "close", str(number),
                    "--reason", "completed",
                    "--comment", message,
                ],
                check=True,
            )
            closed.add(number)
            print(f"Closed resolved auto issue #{number}: {issue.get('title')}")

    print(f"Closed {len(closed)} resolved auto-generated issue(s).")
    return len(closed)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", type=Path, default=Path("build-results"))
    parser.add_argument("--run-url", default=os.environ.get("RUN_URL", ""))
    args = parser.parse_args()
    close_resolved(args.directory, args.run_url)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
