"""Collect build outcomes and base-APK provenance for release notes."""

from __future__ import annotations

import json
import os
from pathlib import Path


SOURCE_LABELS = {
    "morphe": "Morphe",
    "revanced-anddea": "Anddea",
    "rushiranpise": "rushiranpise",
    "hoomans": "arandomhooman",
    "rookie": "RookieEnough",
    "durgesh0505": "durgesh0505",
    "icysymmetra": "icysymmetra",
    "ajstrick81": "ajstrick81",
    "andrewliang25": "andrewliang25",
    "hoo-dles": "hoo-dles",
    "fluffy": "rabilrbl",
    "quantro": "Quantro100",
    "lain": "kiraio-moe",
    "jason": "jasonwu1994",
    "adobo": "jkennethcarino",
    "morning-entree": "Entree3k",
    "bholey": "BholeyKaBhakt",
    "paresh": "Paresh-Maheshwari",
    "dh6k": "dh6k",
}


def _source_label(source: str) -> str:
    return SOURCE_LABELS.get(source, source)


def _cell(value: object) -> str:
    return str(value or "-").replace("|", r"\|").replace("\n", " ")


def _expected_matrix() -> list[dict]:
    value = json.loads(os.environ.get("EXPECTED_MATRIX") or "[]")
    if not isinstance(value, list):
        raise ValueError("EXPECTED_MATRIX must be a JSON array")
    return [item for item in value if isinstance(item, dict)]


def _build_outcomes(
    expected: list[dict], artifact_root: Path
) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    succeeded: list[tuple[str, str]] = []
    failed: list[tuple[str, str]] = []
    for item in expected:
        app = str(item["app_name"])
        source = str(item["source"])
        artifact = artifact_root / f"apk-{app}-{source}"
        target = (
            succeeded
            if artifact.exists() and any(artifact.rglob("*.apk"))
            else failed
        )
        target.append((app, source))
    return succeeded, failed


def _apk_origins(artifact_root: Path) -> list[dict]:
    origins: list[dict] = []
    for metadata_path in artifact_root.rglob("apk-sources.json"):
        try:
            data = json.loads(metadata_path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                origins.extend(item for item in data if isinstance(item, dict))
        except (OSError, json.JSONDecodeError) as error:
            print(f"::warning::Could not read {metadata_path}: {error}")
    return origins


def _build_reports(report_root: Path) -> list[dict]:
    reports: list[dict] = []
    for path in report_root.rglob("*.json"):
        if not path.name.endswith(".json") or path.name == "apk-sources.json":
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict) and "app_name" in data and "source" in data:
            reports.append(data)
    return sorted(reports, key=lambda item: (str(item.get("app_name")), str(item.get("source"))))


def _failure_logs(report_root: Path) -> list[tuple[str, str]]:
    logs: list[tuple[str, str]] = []
    for path in sorted(report_root.glob("*.txt")):
        try:
            content = path.read_text(encoding="utf-8")
        except OSError:
            continue
        marker = "traceback_or_error_tail<<EOF"
        if marker not in content:
            continue
        tail = content.split(marker, 1)[1].split("\nEOF", 1)[0].strip()
        if tail:
            logs.append((path.stem, tail))
    return logs


def _unique_origins(origins: list[dict]) -> list[dict]:
    unique: dict[tuple[object, ...], dict] = {}
    for item in origins:
        key = (
            item.get("app_name"),
            item.get("patch_source"),
            item.get("version"),
            item.get("architecture"),
        )
        unique[key] = item
    return [
        unique[key]
        for key in sorted(
            unique,
            key=lambda value: tuple(str(part or "") for part in value),
        )
    ]


def render(
    succeeded: list[tuple[str, str]],
    failed: list[tuple[str, str]],
    origins: list[dict],
    reports: list[dict] | None = None,
    failure_logs: list[tuple[str, str]] | None = None,
) -> str:
    lines = [
        "",
        "## Build results",
        "",
        f"- Successful: {len(succeeded)}",
        f"- Failed: {len(failed)}",
    ]
    if failed:
        lines.extend(["", "Failed builds:", ""])
        lines.extend(
            f"- `{app}` with `{_source_label(source)}`" for app, source in failed
        )

    if reports:
        lines.extend([
            "",
            "## Patch application details",
            "",
            "| App | Patch source | Status | Applied patches | Excluded patches |",
            "| --- | --- | --- | --- | --- |",
        ])
        for report in reports:
            applied = report.get("applied_patches") or []
            excluded = report.get("feature_failures") or report.get("excluded_patches") or []
            applied_text = ", ".join(f"`{_cell(item)}`" for item in applied) or "-"
            excluded_text = ", ".join(
                f"`{_cell(item.get('name'))}` ({_cell(item.get('reason'))})"
                for item in excluded
                if isinstance(item, dict)
            ) or "-"
            lines.append(
                f"| {_cell(report.get('app_name'))} | "
                f"{_cell(report.get('source_name') or _source_label(str(report.get('source') or '')))} | "
                f"{_cell(report.get('lifecycle_status') or report.get('status'))} | "
                f"{applied_text} | {excluded_text} |"
            )

    if failure_logs:
        lines.extend(["", "## Failure details", ""])
        for name, log in failure_logs:
            lines.extend([f"### {name}", "", "```text", log, "```", ""])

    if origins:
        lines.extend(
            [
                "",
                "## Base APK download sources",
                "",
                "| App | Patch source | Version | Architecture | APK source |",
                "| --- | --- | --- | --- | --- |",
            ]
        )
        for item in _unique_origins(origins):
            label = _cell(item.get("provider_label") or item.get("provider"))
            provider_url = item.get("provider_url")
            provider = f"[{label}]({provider_url})" if provider_url else label
            if item.get("cached"):
                provider += " (cached)"
            lines.append(
                "| {app} | {patch} | {version} | {arch} | {provider} |".format(
                    app=_cell(item.get("app_name")),
                    patch=_cell(
                        _source_label(str(item.get("patch_source") or ""))
                    ),
                    version=_cell(item.get("version")),
                    arch=_cell(item.get("architecture")),
                    provider=provider,
                )
            )
    return "\n".join(lines) + "\n"


def _append(path: str | None, content: str) -> None:
    if not path:
        return
    with Path(path).open("a", encoding="utf-8") as stream:
        stream.write(content)


def main() -> None:
    artifact_root = Path(os.environ.get("ARTIFACT_ROOT", "all-apks"))
    report_root = Path(os.environ.get("REPORT_ROOT", "build-results"))
    succeeded, failed = _build_outcomes(_expected_matrix(), artifact_root)
    content = render(
        succeeded,
        failed,
        _apk_origins(artifact_root),
        _build_reports(report_root),
        _failure_logs(report_root),
    )
    Path(os.environ.get("BUILD_STATUS_PATH", "build_status.md")).write_text(
        content, encoding="utf-8"
    )
    _append(os.environ.get("GITHUB_STEP_SUMMARY"), content)
    _append(os.environ.get("GITHUB_OUTPUT"), f"failed_count={len(failed)}\n")
    if failed:
        print(
            "::warning::Partial release: "
            + ", ".join(f"{app}/{source}" for app, source in failed)
        )


if __name__ == "__main__":
    main()
