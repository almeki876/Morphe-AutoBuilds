"""Create or update GitHub issues when build tools fail to download for a source."""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

FAILURES_FILE = Path("build-metadata/tool-download-failures.json")
PATCH_CONFIG_FILE = Path("my-patch-config.json")
SOURCES_DIR = Path("sources")
ISSUE_PREFIX = "[Source Unavailable]"


def _load_failures() -> dict[str, dict]:
    if not FAILURES_FILE.is_file():
        return {}
    try:
        data = json.loads(FAILURES_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _get_affected_apps(source_name: str) -> list[str]:
    if not PATCH_CONFIG_FILE.is_file():
        return []
    try:
        data = json.loads(PATCH_CONFIG_FILE.read_text(encoding="utf-8"))
        return [
            entry["app_name"]
            for entry in data.get("patch_list", [])
            if entry.get("source") == source_name and entry.get("enabled", True)
        ]
    except Exception:
        return []


def _get_source_repos(source_name: str) -> list[str]:
    path = SOURCES_DIR / f"{source_name}.json"
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return [
                f"{r.get('user')}/{r.get('repo')}"
                for r in data[1:]
                if isinstance(r, dict) and r.get("user") and r.get("repo")
            ]
        return []
    except Exception:
        return []


STATUS_FILE = Path("build-metadata/tool-download-status.json")


def _load_status() -> dict:
    if not STATUS_FILE.is_file():
        return {}
    try:
        data = json.loads(STATUS_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _get_issues(state: str = "open") -> list[dict]:
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not token:
        logging.info("GITHUB_TOKEN not set; skipping issue listing")
        return []
    try:
        result = subprocess.run(
            ["gh", "issue", "list", "--state", state, "--json", "number,title,state"],
            capture_output=True,
            text=True,
            check=True,
        )
        return json.loads(result.stdout)
    except Exception as e:
        logging.warning("Could not list %s issues: %s", state, e)
        return []


def main() -> int:
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not token:
        logging.info("GITHUB_TOKEN not set; skipping issue reporting")
        return 0

    failures = _load_failures()
    status = _load_status()
    succeeded_sources = status.get("succeeded", {})
    all_issues = _get_issues(state="all")

    # 1. Report open issues for failed sources
    for source_name, info in failures.items():
        title = f"{ISSUE_PREFIX} Build tools failed to download for {source_name}"
        reasons = info.get("reasons", ["Download failed"])
        repos = _get_source_repos(source_name)
        affected = _get_affected_apps(source_name)

        body_lines = [
            f"# Upstream Source Unavailable: `{source_name}`",
            "",
            f"Automated tool download failed for patch source `{source_name}` during workflow execution.",
            "The build workflow excluded this source and continued building apps for all other available sources.",
            "",
            "## Details",
            f"- **Source**: `{source_name}`",
            f"- **Repositories**: {', '.join(f'`{r}`' for r in repos) or 'None'}",
            f"- **Affected Apps**: {', '.join(f'`{a}`' for a in affected) or 'None'}",
            "- **Failure Reasons**:",
        ]
        for r in reasons:
            body_lines.append(f"  - `{r}`")

        body_lines.extend([
            "",
            "## Action Required",
            f"- Check if upstream repository access is blocked (e.g. GitHub TOS) or if release assets were renamed/removed.",
            f"- If the repository moved or was replaced, update `sources/{source_name}.json` or remove the source from `my-patch-config.json`.",
            "- Once access is restored or a working patch bundle cache is present, this issue will close automatically.",
        ])

        body_path = Path(f"issue-source-{source_name}.md")
        body_path.write_text("\n".join(body_lines), encoding="utf-8")

        existing_issue = None
        for issue in all_issues:
            if issue.get("title") == title:
                existing_issue = issue
                break

        try:
            if existing_issue:
                num = str(existing_issue.get("number"))
                is_closed = str(existing_issue.get("state") or "").upper() == "CLOSED"
                if is_closed:
                    subprocess.run(
                        ["gh", "issue", "reopen", num, "--comment", "Reopening: source remains unavailable during workflow execution."],
                        check=True,
                    )
                    logging.info("Reopened issue #%s: %s", num, title)
                subprocess.run(
                    ["gh", "issue", "comment", num, "--body-file", str(body_path)],
                    check=True,
                )
                logging.info("Updated existing issue #%s: %s", num, title)
            else:
                subprocess.run(
                    ["gh", "issue", "create", "--title", title, "--body-file", str(body_path), "--label", "bug"],
                    check=True,
                )
                logging.info("Created new issue: %s", title)
        except Exception as e:
            logging.warning("Failed to create/update issue for %s: %s", source_name, e)
        finally:
            body_path.unlink(missing_ok=True)

    # 2. Close issues ONLY for sources that genuinely succeeded or were deleted from sources/
    open_issues = [i for i in all_issues if str(i.get("state") or "").upper() == "OPEN"]
    for issue in open_issues:
        issue_title = str(issue.get("title") or "")
        if issue_title.startswith(ISSUE_PREFIX):
            match = re.search(r"for\s+([A-Za-z0-9_-]+)\s*$", issue_title)
            if match:
                sname = match.group(1)
                num = str(issue.get("number"))
                source_file_exists = (SOURCES_DIR / f"{sname}.json").is_file()

                # Close if:
                # 1) The source succeeded in downloading this run (recovered!)
                # 2) OR the source definition was deleted from the repository
                if sname in succeeded_sources:
                    try:
                        subprocess.run(
                            [
                                "gh", "issue", "close", num,
                                "--comment", f"Recovered automatically: build tools for `{sname}` downloaded successfully.",
                            ],
                            check=True,
                        )
                        logging.info("Closed recovered issue #%s: %s", num, issue_title)
                    except Exception as e:
                        logging.warning("Failed to close issue #%s: %s", num, e)
                elif not source_file_exists:
                    try:
                        subprocess.run(
                            [
                                "gh", "issue", "close", num,
                                "--comment", f"Closed: source `{sname}` was removed from repository configuration.",
                            ],
                            check=True,
                        )
                        logging.info("Closed removed-source issue #%s: %s", num, issue_title)
                    except Exception as e:
                        logging.warning("Failed to close issue #%s: %s", num, e)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
