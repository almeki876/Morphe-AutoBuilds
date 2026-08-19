"""Create or update one GitHub issue per failing app and phase."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path

from src.providers import configured_package


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
        values["log"] = text.split(marker, 1)[1].split("\nEOF", 1)[0].strip()
    return values


def _report(report_root: Path, app: str, source: str) -> dict:
    report = report_root / f"{app}-{source}.json"
    try:
        value = json.loads(report.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


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


def _publish(title: str, body: str) -> None:
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


def _body(status: dict[str, str], report_root: Path) -> str:
    app = status["app"]
    source = status["source"]
    phase = status.get("phase", "Build")
    package = configured_package(app) or "unknown"
    version = _version(status, report_root, app, source)
    report = _report(report_root, app, source)
    source_name = status.get("source_name") or report.get("source_name") or source
    log = status.get("log", "No raw log excerpt was captured.")[-12000:]
    return f"""# Build failure report

- **App:** `{app}`
- **Package:** `{package}`
- **Patch source:** `{source_name}`
- **Phase:** `{phase}`
- **Attempted APK version:** `{version}`
- **Workflow run:** {status.get('run', 'unknown')}

## Hypothesis

{_hypothesis(log, phase)}

## Raw log excerpt

```text
{log}
```

This issue is updated instead of duplicated when the same app and phase fail again.
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", type=Path, default=Path("failure-results"))
    parser.add_argument("--report-root", type=Path, default=Path("failure-results"))
    args = parser.parse_args()
    statuses = sorted(args.directory.rglob("*.txt"))
    failures = []
    for path in statuses:
        status = _read_status(path)
        if status.get("status") == "failure":
            failures.append(status)
    for status in failures:
        phase = status.get("phase", "Build")
        title = f"[Build Failure] {status['app']} - {phase}"
        _publish(title, _body(status, args.report_root))
    print(f"Processed {len(failures)} failed app phase(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
