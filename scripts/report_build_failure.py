"""Create or update one GitHub issue per failing app and phase."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.providers import configured_package


DRY_RUN = False


VERSION_PATTERNS = (
    re.compile(r"compatible version\s+([^\s]+)", re.IGNORECASE),
    re.compile(r"(?:version|v)[=:\s]+([0-9][^\s,)]*)", re.IGNORECASE),
)


def _read_status(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    text = path.read_text(encoding="utf-8", errors="replace")
    for line in text.splitlines():
        if "=" in line and not line.startswith("traceback_or_error_tail"):
            key, value = line.split("=", 1)
            values[key] = value
    marker = "traceback_or_error_tail<<EOF"
    if marker in text:
        captured = text.split(marker, 1)[1].split("\nEOF", 1)[0].strip()
        values["log"] = captured or text[-16000:].strip()
    else:
        values["log"] = text[-16000:].strip()
    return values


def _report(report_root: Path, app: str, source: str) -> dict:
    report = report_root / f"{app}-{source}.json"
    try:
        value = json.loads(report.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _patch_source_url(source: str) -> str:
    config_path = Path("sources") / f"{source}.json"
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "unknown"
    if isinstance(config, dict) and config.get("bundle_url"):
        return str(config["bundle_url"])
    if isinstance(config, list):
        for item in config:
            if isinstance(item, dict) and item.get("user") and item.get("repo"):
                return f"https://github.com/{item['user']}/{item['repo']}"
    return "unknown"


def _version(status: dict[str, str], report_root: Path, app: str, source: str) -> str:
    value = _report(report_root, app, source).get("version")
    if value:
        return str(value)
    for pattern in VERSION_PATTERNS:
        match = pattern.search(status.get("log", ""))
        if match:
            return match.group(1)
    return "unknown"


def _hypothesis(log: str, phase: str) -> str:
    lowered = log.casefold()
    if "403" in lowered or "429" in lowered or "bot protection" in lowered:
        return "APK provider rate limiting or bot protection prevented the download."
    if "fingerprint mismatch" in lowered or "severe" in lowered and "fingerprint" in lowered:
        return "The requested APK version likely does not match the patch or package fingerprint."
    if "404" in lowered or "no download link" in lowered:
        return "The provider or upstream release may have removed or renamed the requested asset."
    if phase.casefold() in {"patch", "build"} and "patch" in lowered:
        return "The patch bundle and APK may be incompatible, or an upstream patch regression occurred."
    if "apksigner" in lowered or "signing" in lowered:
        return "APK signing or keystore/tool compatibility failed."
    return "The failure needs inspection of the attached raw log excerpt."


def _issue_number(title: str) -> str | None:
    result = subprocess.run(
        [
            "gh", "issue", "list", "--state", "open", "--search",
            f"{title} in:title", "--json", "number,title",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    for item in json.loads(result.stdout or "[]"):
        if item.get("title") == title:
            return str(item["number"])
    return None


def _failure_summary(log: str) -> str:
    patterns = (
        r"(?:FAILED|ERROR|FATAL):\s*(.+)",
        r"(?:Error|Exception):\s*(.+)",
    )
    for pattern in patterns:
        match = re.search(pattern, log, re.IGNORECASE)
        if match:
            summary = re.sub(r"\s+", " ", match.group(1)).strip()
            if summary:
                return summary[:100]
    return "unspecified error"


def _open_issues(search: str) -> list[dict]:
    result = subprocess.run(
        ["gh", "issue", "list", "--state", "open", "--search", search,
         "--json", "number,title"],
        check=True,
        capture_output=True,
        text=True,
    )
    value = json.loads(result.stdout or "[]")
    return value if isinstance(value, list) else []


def _issue_body(number: str) -> str:
    result = subprocess.run(
        ["gh", "issue", "view", number, "--json", "body", "--jq", ".body"],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout


def _same_target(body: str, app: str, source_name: str) -> bool:
    return (
        f"- **App:** `{app}`" in body
        and f"- **Patch source:** `{source_name}`" in body
    )


def _publish(title: str, body: str) -> None:
    if DRY_RUN:
        print(f"\n--- DRY RUN: {title} ---\n{body}\n--- END DRY RUN ---")
        return
    body_path = Path("build-failure-issue.md")
    body_path.write_text(body, encoding="utf-8")
    existing = _issue_number(title)
    if existing:
        subprocess.run(
            ["gh", "issue", "comment", existing, "--body-file", str(body_path)],
            check=True,
        )
        print(f"Updated existing issue #{existing}: {title}")
        return
    subprocess.run(
        ["gh", "issue", "create", "--title", title, "--body-file", str(body_path)],
        check=True,
    )
    print(f"Created issue: {title}")


def _feature_body(
    report: dict,
    status: dict[str, str],
    patch: dict,
) -> str:
    app = str(report.get("app_name") or status.get("app"))
    source = str(report.get("source_name") or status.get("source"))
    version = report.get("version") or _version(
        status, Path("."), app, str(report.get("source") or status.get("source"))
    )
    log = status.get("log", "No raw log excerpt was captured.")[-16000:]
    options = [
        f"- `{item.get('patch')}`: `{item.get('key')}={item.get('value')}`"
        for item in report.get("requested_options", [])
        if isinstance(item, dict)
    ]
    options_text = "\n".join(options) or "- None recorded"
    return f"""# Feature patch failure

- **App:** `{app}`
- **Package:** `{configured_package(app) or 'unknown'}`
- **Patch source:** `{source}`
- **Failed patch feature:** `{patch.get('name', 'unknown')}`
- **APK version:** `{version}`
- **Workflow run:** {status.get('run', 'unknown')}

## Required follow-up

This patch was temporarily disabled or could not be selected so the build could complete, but it is intended to be applied. This is a feature failure, not a permanent waiver. Restore the patch after the upstream patch or APK compatibility problem is fixed.

## Reason

{patch.get('reason', 'The requested patch was not applied.')}

## Requested patch options

{options_text}

## Raw build log

```text
{log}
```
"""


def _close_related(prefix: str, app: str, source_name: str, message: str) -> None:
    if DRY_RUN:
        return
    for issue in _open_issues(f"{prefix} {app} in:title"):
        number = str(issue.get("number"))
        title = str(issue.get("title") or "")
        if not title.startswith(prefix) or not _same_target(
            _issue_body(number), app, source_name
        ):
            continue
        subprocess.run(
            ["gh", "issue", "close", number, "--comment", message],
            check=True,
        )
        print(f"Closed issue #{number}: {title}")


def _manage_feature_lifecycle(
    report: dict,
    status: dict[str, str],
) -> None:
    app = str(report.get("app_name") or status.get("app"))
    source_name = str(report.get("source_name") or status.get("source"))
    failures = report.get("feature_failures") or []
    if failures:
        for patch in failures:
            patch_name = str(patch.get("name") or "unknown")
            version = report.get("version") or _version(
                status, Path("."), app, str(report.get("source") or status.get("source"))
            )
            source = str(report.get("source_name") or status.get("source"))
            title = f"[Feature Failure] {app} - {source} - v{version} - {patch_name}"
            _publish(title, _feature_body(report, status, patch))
        return

    if report.get("fully_applied") is True:
        message = (
            f"CI verified that all requested patches are applied for {app} "
            f"with source {source_name}. Closing this resolved tracking issue."
        )
        _close_related("[Feature Failure]", app, source_name, message)
        _close_related("[Build Failure]", app, source_name, message)


def _partial_patch_body(report: dict, status: dict[str, str]) -> str:
    app = str(report.get("app_name") or status.get("app") or "unknown")
    package = configured_package(app) or "unknown"
    source = str(report.get("source") or status.get("source") or "unknown")
    source_url = str(report.get("patch_source_url") or _patch_source_url(source))
    failed = [str(item) for item in report.get("failed_patches", []) if str(item).strip()]
    failed_lines = "\n".join(f"  - `{name}`" for name in failed)
    return f"""## 概要
ビルドとリリースは完了しましたが、パッチ適用時に以下のパッチが指紋不一致等の理由でスキップされました。上流のパッチソース（リポジトリ）の対応状況を確認してください。

- **対象アプリ**: {app} ({package})
- **パッチソース**: {source_url}
- **適用に失敗したパッチ**:
{failed_lines}

※このIssueは自動的に作成されました。
"""


def _manage_partial_patch_failures(report_root: Path) -> int:
    statuses = {
        (status.get("app"), status.get("source")): status
        for path in report_root.rglob("*.txt")
        for status in [_read_status(path)]
    }
    created = 0
    for path in sorted(report_root.glob("*.json")):
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(report, dict) or report.get("status") != "success":
            continue
        failed = report.get("failed_patches") or []
        if not failed:
            continue
        app = str(report.get("app_name") or "unknown")
        source_name = str(report.get("source_name") or report.get("source") or "unknown")
        title = f"[Partial Patch Failure] {app} ({source_name}) - 一部パッチ適用失敗"
        _publish(title, _partial_patch_body(report, statuses.get((report.get("app_name"), report.get("source")), {})))
        created += 1
    return created


def _body(status: dict[str, str], report_root: Path) -> str:
    app = status["app"]
    source = status["source"]
    phase = status.get("phase", "Build")
    package = configured_package(app) or "unknown"
    version = _version(status, report_root, app, source)
    report = _report(report_root, app, source)
    source_name = status.get("source_name") or report.get("source_name") or source
    log = status.get("log", "No raw log excerpt was captured.")[-12000:]
    skipped = report.get("excluded_patches") or report.get("feature_failures") or []
    skipped_lines = []
    for item in skipped:
        if isinstance(item, dict):
            name = item.get("name", "unknown")
            reason = item.get("reason", "excluded by configuration or patch defaults")
            skipped_lines.append(f"- `{name}`: {reason}")
        else:
            skipped_lines.append(f"- `{item}`")
    skipped_text = "\n".join(skipped_lines) or "- None recorded"
    return f"""# Build failure report

- **App:** `{app}`
- **Package:** `{package}`
- **Patch source:** `{source_name}`
- **Phase:** `{phase}`
- **Attempted APK version:** `{version}`
- **Occurred at (UTC):** `{datetime.now(timezone.utc).isoformat()}`
- **Workflow run:** {status.get('run', 'unknown')}

## Hypothesis

{_hypothesis(log, phase)}

## Raw log excerpt

```text
{log}
```

## Skipped or excluded patches

{skipped_text}

This issue is updated instead of duplicated when the same app, source, version, phase, and failure summary recur.
"""


def _failure_title(status: dict[str, str], report_root: Path) -> str:
    app = status["app"]
    source = status.get("source", "unknown")
    phase = status.get("phase", "Build")
    version = _version(status, report_root, app, source)
    summary = _failure_summary(status.get("log", ""))
    return f"[Build Failure] {app} - {source} - {phase} - v{version} - {summary}"


def main() -> int:
    global DRY_RUN
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", type=Path, default=Path("failure-results"))
    parser.add_argument("--report-root", type=Path, default=Path("failure-results"))
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Render issue updates without calling GitHub CLI.",
    )
    parser.add_argument(
        "--partial-only",
        action="store_true",
        help="Report successful releases with silently failed patches only.",
    )
    args = parser.parse_args()
    DRY_RUN = args.dry_run
    if args.partial_only:
        print(f"Processed {_manage_partial_patch_failures(args.directory)} partial patch failure(s).")
        return 0
    statuses = sorted(args.directory.rglob("*.txt"))
    failures = []
    for path in statuses:
        status = _read_status(path)
        if status.get("status") == "failure":
            failures.append(status)
    for status in failures:
        phase = status.get("phase", "Build")
        title = _failure_title(status, args.report_root)
        _publish(title, _body(status, args.report_root))

    status_by_target = {
        (item.get("app"), item.get("source")): item
        for item in (_read_status(path) for path in statuses)
    }
    for path in sorted(args.report_root.glob("*.json")):
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(report, dict) or "app_name" not in report:
            continue
        key = (report.get("app_name"), report.get("source"))
        _manage_feature_lifecycle(report, status_by_target.get(key, {}))
    print(f"Processed {len(failures)} failed app phase(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
