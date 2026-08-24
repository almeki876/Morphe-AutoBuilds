"""Write per-matrix patch status artifacts and a readable Actions job summary."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path


SKIP_RE = re.compile(
    r"Skipping disabled:\s*(?P<name>.+?)\s*\((?P<reason>[^)]*)\)",
    re.IGNORECASE,
)
UNSUPPORTED_RE = re.compile(
    r'["“](?P<name>.+?)["”]\s+is not supported in this version\.?(?P<detail>.*)',
    re.IGNORECASE,
)


def tail_text(path: Path, lines: int) -> str:
    if not path.is_file():
        return ""
    return "\n".join(
        path.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:]
    )


def runtime_patch_outcomes(build_log: Path) -> list[dict[str, str]]:
    """Extract runtime-only patch outcomes from the complete patch CLI log."""
    if not build_log.is_file():
        return []

    found: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for line in build_log.read_text(encoding="utf-8", errors="replace").splitlines():
        skip = SKIP_RE.search(line)
        if skip:
            name = skip.group("name").strip()
            raw_reason = skip.group("reason").strip()
            category = (
                "runtime-skipped-default"
                if "default" in raw_reason.casefold()
                else "runtime-skipped"
            )
            reason = raw_reason or "CLI skipped this patch at runtime"
            key = (name, category)
            if name and key not in seen:
                seen.add(key)
                found.append({"name": name, "category": category, "reason": reason})
            continue

        unsupported = UNSUPPORTED_RE.search(line)
        if unsupported:
            name = unsupported.group("name").strip()
            detail = unsupported.group("detail").strip()
            reason = "not supported in this APK version"
            if detail:
                reason += f". {detail}"
            key = (name, "unsupported")
            if name and key not in seen:
                seen.add(key)
                found.append(
                    {"name": name, "category": "unsupported", "reason": reason}
                )
    return found


def _failure_entry(name: object, reason: object) -> dict[str, str] | None:
    patch = str(name or "").strip()
    if not patch:
        return None
    return {"name": patch, "reason": str(reason or "-").strip() or "-"}


def enrich_report(report: dict, build_log: Path) -> dict:
    """Preserve configured exclusions and add runtime skip/unsupported/failure facts.

    ``generate_release_details`` already renders ``feature_failures`` under the
    user-facing not-applied section.  Enriching the artifact copy here keeps the
    main Release path self-contained and makes the evidence available before the
    GitHub Release is published, without depending on a recovery workflow.
    """
    enriched = dict(report)
    failures: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()

    for collection in (
        report.get("feature_failures") or [],
        report.get("excluded_patches") or [],
    ):
        if not isinstance(collection, list):
            continue
        for item in collection:
            if not isinstance(item, dict):
                continue
            entry = _failure_entry(item.get("name"), item.get("reason"))
            if entry is None:
                continue
            key = (entry["name"], entry["reason"])
            if key not in seen:
                seen.add(key)
                failures.append(entry)

    for item in runtime_patch_outcomes(build_log):
        reason = f"[{item['category']}] {item['reason']}"
        entry = _failure_entry(item["name"], reason)
        if entry is None:
            continue
        key = (entry["name"], entry["reason"])
        if key not in seen:
            seen.add(key)
            failures.append(entry)

    failed = report.get("failed_patches") or []
    if isinstance(failed, list):
        for name in failed:
            entry = _failure_entry(name, "[failed] CLI reported patch application failure")
            if entry is None:
                continue
            key = (entry["name"], entry["reason"])
            if key not in seen:
                seen.add(key)
                failures.append(entry)

    enriched["feature_failures"] = failures
    return enriched


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
        lines.extend(["", "**Excluded / runtime-not-applied patches**"])
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
            report = enrich_report(payload, build_log)
            (directory / f"{app}-{source}.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

    summary = os.getenv("GITHUB_STEP_SUMMARY")
    if summary:
        with Path(summary).open("a", encoding="utf-8") as handle:
            handle.write(status_body)
            handle.write(patch_summary(report, build_log))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
