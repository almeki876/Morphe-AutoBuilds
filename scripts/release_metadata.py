"""Collect build outcomes and base-APK provenance for release notes."""

from __future__ import annotations

import json
import os
from pathlib import Path


SOURCE_LABELS = {
    "morphe": "Morphe",
    "revanced-anddea": "Anddea",
    "hoo": "rushiranpise",
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
    succeeded, failed = _build_outcomes(_expected_matrix(), artifact_root)
    content = render(succeeded, failed, _apk_origins(artifact_root))
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
