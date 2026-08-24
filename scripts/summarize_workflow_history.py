"""Summarize the latest completed GitHub Actions run for each current workflow."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"


def _load_runs(path: Path) -> list[dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    pages = payload if isinstance(payload, list) else [payload]
    runs: list[dict] = []
    for page in pages:
        if isinstance(page, dict):
            runs.extend(page.get("workflow_runs") or [])
    return runs


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("runs_json", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--current-run-id", type=int, default=0)
    args = parser.parse_args()

    current_paths = sorted(
        path.relative_to(ROOT).as_posix()
        for path in list(WORKFLOWS.glob("*.yml")) + list(WORKFLOWS.glob("*.yaml"))
    )
    runs = _load_runs(args.runs_json)

    latest: dict[str, dict] = {}
    for run in sorted(runs, key=lambda item: item.get("created_at") or "", reverse=True):
        path = run.get("path") or ""
        if path not in current_paths or path in latest:
            continue
        if int(run.get("id") or 0) == args.current_run_id:
            continue
        if run.get("status") != "completed":
            continue
        latest[path] = run

    lines = [
        "## Recent workflow health",
        "",
        "Latest completed run per workflow (excluding this audit run).",
        "",
        "| Workflow | Run | Event | Conclusion | Created |",
        "|---|---:|---|---|---|",
    ]
    failures = 0
    for path in current_paths:
        run = latest.get(path)
        name = Path(path).name
        if not run:
            lines.append(f"| `{name}` | — | — | no completed run found | — |")
            continue
        conclusion = run.get("conclusion") or "unknown"
        if conclusion == "failure":
            failures += 1
        run_id = run.get("id")
        url = run.get("html_url") or ""
        run_cell = f"[{run_id}]({url})" if url else str(run_id)
        lines.append(
            f"| `{name}` | {run_cell} | `{run.get('event')}` | **{conclusion}** | `{run.get('created_at')}` |"
        )

    lines.extend(
        [
            "",
            f"Current workflow files: **{len(current_paths)}**; latest failures visible in fetched history: **{failures}**.",
            "",
            "A historical failure is reported here but does not fail this CI by itself. "
            "Static workflow policy, actionlint, repository validation, and unit tests are the gating checks.",
        ]
    )
    text = "\n".join(lines) + "\n"
    if args.output:
        args.output.write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
