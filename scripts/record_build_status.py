"""Write per-matrix patch status artifacts and a readable Actions job summary."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path


def tail_text(path: Path, lines: int) -> str:
    if not path.is_file():
        return ""
    return "\n".join(path.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:])


def patch_summary(report: dict | None, build_log: Path) -> str:
    lines = ["", "### Patch application details"]
    if report:
        lines.extend(
            [
                f"- **Patch source:** `{report.get('source_name') or report.get('source') or '-'}`",
                f"- **Status:** `{report.get('lifecycle_status') or report.get('status') or '-'}`",
                "",
                "**Applied patches**",
            ]
        )
        applied = report.get("applied_patches") or []
        lines.extend(f"- `{name}`" for name in applied)
        lines.extend(["", "**Excluded patches**"])
        excluded = report.get("feature_failures") or report.get("excluded_patches") or []
        for item in excluded:
            if isinstance(item, dict):
                lines.append(f"- `{item.get('name') or '-'}`: {item.get('reason') or '-'}")
    else:
        lines.append("- No patch report was produced before the failure.")

    tail = tail_text(build_log, 80)
    if tail:
        lines.extend(["", "**Error log excerpt**", "```text", tail, "```"])
    return "\n".join(lines) + "\n"


def main() -> int:
    app = os.environ["APP_NAME"]
    source = os.environ["SOURCE"]
    source_name = os.environ["SOURCE_NAME"]
    status = os.environ.get("BUILD_STATUS", "unknown")
    run_url = os.environ["RUN_URL"]

    directory = Path("build-status")
    directory.mkdir(parents=True, exist_ok=True)
    status_path = directory / f"{app}-{source}-build.txt"
    build_log = Path("build.log")
    tail = tail_text(build_log, 80)
    fields = [
        f"app={app}",
        f"source={source}",
        f"source_name={source_name}",
        "phase=Patch",
        f"status={status}",
        f"run={run_url}",
    ]
    if tail:
        fields.extend(["", "traceback_or_error_tail<<EOF", tail, "EOF"])
    status_body = "\n".join(fields) + "\n"
    status_path.write_text(status_body, encoding="utf-8")

    report_path = Path("build-metadata/build-report.json")
    report: dict | None = None
    if report_path.is_file():
        payload = json.loads(report_path.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            report = payload
        shutil.copy2(report_path, directory / f"{app}-{source}.json")

    summary = os.getenv("GITHUB_STEP_SUMMARY")
    if summary:
        with Path(summary).open("a", encoding="utf-8") as handle:
            handle.write(status_body)
            handle.write(patch_summary(report, build_log))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
