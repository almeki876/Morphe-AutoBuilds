"""Append runtime skip/unsupported evidence from the source Actions log.

Release detail pages already distinguish actually Applied patches from explicit
configuration exclusions.  This helper adds the remaining runtime-only facts
that are visible in the patch CLI log but are not represented by configuration:

* ``Skipping disabled: <patch> (<reason>)``
* ``\"<patch>\" is not supported in this version ...``
* patch names recorded in ``failed_patches``

The full workflow log is treated as evidence.  Entries are scoped to the exact
Build <app> with <source label> job before being written to that variant's
``patches.md`` page.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


SKIP_RE = re.compile(r"Skipping disabled:\s*(?P<name>.+?)\s*\((?P<reason>[^)]*)\)", re.IGNORECASE)
UNSUPPORTED_RE = re.compile(
    r'["“](?P<name>.+?)["”]\s+is not supported in this version\.?(?P<detail>.*)',
    re.IGNORECASE,
)


def _read_json(path: Path) -> dict | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _load_reports(root: Path) -> list[dict]:
    reports: list[dict] = []
    if not root.exists():
        return reports
    for path in root.rglob("*.json"):
        payload = _read_json(path)
        if payload and payload.get("app_name") and payload.get("source"):
            reports.append(payload)
    return reports


def _source_labels(root: Path) -> dict[tuple[str, str], str]:
    labels: dict[tuple[str, str], str] = {}
    if not root.exists():
        return labels
    for path in root.rglob("*-build.txt"):
        values: dict[str, str] = {}
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line in lines:
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            if key in {"app", "source", "source_name"}:
                values[key] = value.strip()
        app = values.get("app")
        source = values.get("source")
        label = values.get("source_name")
        if app and source and label:
            labels[(app, source)] = label
    return labels


def _job_name_from_log_line(line: str) -> str:
    # `gh run view --log` emits tab-separated job/step/timestamp/message fields.
    # Keeping this deliberately conservative avoids attributing another job's
    # warning to the wrong app when output formatting changes.
    return line.split("\t", 1)[0].strip()


def collect_runtime_skips(
    log_text: str,
    *,
    app: str,
    source_label: str,
) -> list[dict[str, str]]:
    expected_job = f"Build {app} with {source_label}".casefold()
    found: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()

    for raw_line in log_text.splitlines():
        job_name = _job_name_from_log_line(raw_line)
        if expected_job not in job_name.casefold():
            continue

        skip = SKIP_RE.search(raw_line)
        if skip:
            name = skip.group("name").strip()
            raw_reason = skip.group("reason").strip()
            category = "default-disabled" if "default" in raw_reason.casefold() else "disabled"
            reason = f"CLI: Skipping disabled ({raw_reason or 'reason not reported'})"
            key = (name, category, reason)
            if key not in seen:
                seen.add(key)
                found.append({"name": name, "category": category, "reason": reason})
            continue

        unsupported = UNSUPPORTED_RE.search(raw_line)
        if unsupported:
            name = unsupported.group("name").strip()
            detail = unsupported.group("detail").strip()
            reason = "CLI: not supported in this APK version"
            if detail:
                reason += f". {detail}"
            key = (name, "unsupported", reason)
            if key not in seen:
                seen.add(key)
                found.append({"name": name, "category": "unsupported", "reason": reason})

    return found


def _append_page(path: Path, observations: list[dict[str, str]], failed: list[str]) -> None:
    rows: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in observations:
        name = str(item.get("name") or "").strip()
        category = str(item.get("category") or "runtime-skip").strip()
        reason = str(item.get("reason") or "CLI skipped this patch").strip()
        if not name:
            continue
        key = (name, category)
        if key not in seen:
            seen.add(key)
            rows.append((name, category, reason))
    for name in failed:
        name = str(name).strip()
        key = (name, "failed")
        if name and key not in seen:
            seen.add(key)
            rows.append((name, "failed", "CLI reported patch application failure"))

    if not rows:
        return

    lines = [
        "",
        "## Runtime skipped / unsupported / failed patches",
        "",
        "この表は設定値ではなく、Releaseを生成したGitHub Actionsの実ログから抽出しています。",
        "",
        "| Patch | Outcome | Evidence / reason |",
        "| --- | --- | --- |",
    ]
    for name, category, reason in rows:
        safe_name = name.replace("|", r"\|")
        safe_reason = reason.replace("|", r"\|")
        lines.append(f"| `{safe_name}` | `{category}` | {safe_reason} |")
    with path.open("a", encoding="utf-8") as stream:
        stream.write("\n".join(lines) + "\n")


def append_details(actions_log: Path, build_results: Path, release_dir: Path) -> int:
    log_text = actions_log.read_text(encoding="utf-8", errors="replace")
    reports = _load_reports(build_results)
    labels = _source_labels(build_results)
    updated = 0

    for report in reports:
        if report.get("status") != "success":
            continue
        app = str(report.get("app_name") or "").strip()
        source = str(report.get("source") or "").strip()
        if not app or not source:
            continue
        source_label = labels.get((app, source)) or str(report.get("source_name") or source)
        observations = collect_runtime_skips(log_text, app=app, source_label=source_label)
        failed = [str(name) for name in (report.get("failed_patches") or [])]
        target = release_dir / "apps" / app / "variants" / source / "patches.md"
        if target.is_file() and (observations or failed):
            _append_page(target, observations, failed)
            updated += 1

    return updated


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--actions-log", type=Path, required=True)
    parser.add_argument("--build-results", type=Path, default=Path("details-input/build"))
    parser.add_argument("--release-dir", type=Path, required=True)
    args = parser.parse_args()
    updated = append_details(args.actions_log, args.build_results, args.release_dir)
    print(f"Appended runtime skip evidence to {updated} release variant page(s).")


if __name__ == "__main__":
    main()
