"""Generate persistent, navigable details for one published APK release.

Hierarchy:
  release-details/<tag>/README.md
    -> apps/<app>/README.md
       -> variants/<source>/README.md
          -> patches.md
          -> apk-source.md
          -> virustotal.md
          -> build.md
          -> developer/README.md

Gboard is the deliberate exception: its single ``jason`` matrix variant is an
integrated Jason + Adobo + Morning-Entree build.  Other apps may legitimately
have multiple source variants in the same Release, so they are never collapsed.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from scripts.release_notes import APP_LABELS, SOURCE_LABELS, _source_url


GBOARD_SOURCES = ("jason", "adobo", "morning-entree")
GBOARD_CONFLICT_SUPPRESSED = {
    "adobo": {
        "Enable OCR feature",
        "Enable access points menu redesign",
        "Enable key shape selection",
    },
    "morning-entree": {"Change package name"},
}


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tag", default=os.getenv("RELEASE_TAG", ""))
    parser.add_argument(
        "--repository",
        default=os.getenv("GITHUB_REPOSITORY", "almeki876/Morphe-AutoBuilds"),
    )
    parser.add_argument("--run-url", default=os.getenv("RUN_URL", ""))
    parser.add_argument("--build-results", type=Path, default=Path("details-input/build"))
    parser.add_argument("--download-results", type=Path, default=Path("details-input/download"))
    parser.add_argument("--base-inputs", type=Path, default=Path("details-input/base"))
    parser.add_argument("--virustotal", type=Path, default=Path("virustotal_base_results.json"))
    parser.add_argument("--output-root", type=Path, default=Path("release-details"))
    parser.add_argument("--release-notes", type=Path, default=Path("release-notes-details.md"))
    return parser.parse_args()


def _read_json(path: Path) -> object | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _safe(value: object) -> str:
    return str(value or "-").replace("|", r"\|").replace("\n", " ")


def _label_app(app: str) -> str:
    return APP_LABELS.get(app, app)


def _label_source(source: str) -> str:
    return SOURCE_LABELS.get(source, source)


def _source_display(app: str, source: str) -> str:
    if app == "gboard" and source == "jason":
        return "Jason + Adobo + Morning-Entree (3-source integrated)"
    return _label_source(source)


def _repo_url(repository: str, path: str) -> str:
    encoded = "/".join(quote(part) for part in path.split("/"))
    return f"https://github.com/{repository}/blob/main/{encoded}"


def _load_reports(root: Path) -> list[dict]:
    reports: list[dict] = []
    for path in root.rglob("*.json") if root.exists() else ():
        payload = _read_json(path)
        if isinstance(payload, dict) and payload.get("app_name") and payload.get("source"):
            item = dict(payload)
            item["_path"] = str(path)
            reports.append(item)
    return sorted(
        reports,
        key=lambda item: (str(item.get("app_name")), str(item.get("source"))),
    )


def _load_origins(root: Path) -> list[dict]:
    origins: list[dict] = []
    if not root.exists():
        return origins
    for path in root.rglob("origin.json"):
        payload = _read_json(path)
        if isinstance(payload, dict) and payload.get("app_name"):
            origins.append(dict(payload))
    for path in root.rglob("apk-sources.json"):
        payload = _read_json(path)
        if isinstance(payload, list):
            origins.extend(
                item
                for item in payload
                if isinstance(item, dict) and item.get("app_name")
            )
    unique: dict[tuple[str, str, str], dict] = {}
    for item in origins:
        key = (
            str(item.get("app_name") or ""),
            str(item.get("patch_source") or ""),
            str(item.get("architecture") or ""),
        )
        unique[key] = item
    return list(unique.values())


def _load_vt(path: Path) -> dict:
    payload = _read_json(path)
    return payload if isinstance(payload, dict) else {"results": [], "failures": []}


def _load_patch_config() -> list[dict]:
    payload = _read_json(Path("my-patch-config.json"))
    if not isinstance(payload, dict):
        return []
    return [
        item for item in payload.get("patch_list", []) if isinstance(item, dict)
    ]


def _gboard_supplemental_selections(config: list[dict]) -> dict[str, set[str]]:
    result: dict[str, set[str]] = {}
    for source in GBOARD_SOURCES[1:]:
        requested: set[str] = set()
        for item in config:
            if item.get("app_name") == "gboard" and item.get("source") == source:
                requested = {
                    str(name)
                    for name in item.get("force_enable", [])
                    if str(name).strip()
                }
                break
        result[source] = requested - GBOARD_CONFLICT_SUPPRESSED.get(source, set())
    return result


def _patch_entries(source: str) -> dict[str, dict]:
    payload = _read_json(Path("tools") / source / "patches-list.json")
    raw = payload.get("patches") if isinstance(payload, dict) else payload
    if not isinstance(raw, list):
        return {}
    result: dict[str, dict] = {}
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or "").strip()
        if name:
            result[name.casefold()] = entry
    return result


def _description(entry: dict | None) -> str:
    if not entry:
        return "Upstream metadataに説明がありません。"
    for key in ("description", "shortDescription", "summary"):
        value = entry.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip().replace("\n", " ")
    return "Upstream metadataに説明がありません。"


def _source_tag(source: str) -> str:
    payload = _read_json(Path("last-tags.json"))
    if not isinstance(payload, dict):
        return ""
    aliases = {"revanced-anddea": "anddea"}
    return str(
        payload.get(source) or payload.get(aliases.get(source, "")) or ""
    )


def _patch_source_for(
    app: str,
    default_source: str,
    patch: str,
    gboard_extra: dict[str, set[str]],
) -> str:
    if app != "gboard":
        return default_source
    for source, names in gboard_extra.items():
        if patch in names:
            return source
    return "jason"


def _find_origin(origins: list[dict], app: str, source: str) -> dict | None:
    exact = [
        item
        for item in origins
        if item.get("app_name") == app and item.get("patch_source") == source
    ]
    if exact:
        return exact[-1]
    any_app = [item for item in origins if item.get("app_name") == app]
    return any_app[-1] if any_app else None


def _find_vt(vt: dict, app: str, source: str) -> list[dict]:
    prefix = f"{app}-{source}-"
    results = vt.get("results") if isinstance(vt, dict) else []
    return [
        item
        for item in (results or [])
        if isinstance(item, dict)
        and str(item.get("file") or "").startswith(prefix)
    ]


def _copy_or_text(source: Path | None, target: Path, fallback: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if source is not None and source.is_file():
        shutil.copy2(source, target)
    else:
        target.write_text(fallback, encoding="utf-8")


def _matching_status_file(
    root: Path, app: str, source: str, suffix: str
) -> Path | None:
    candidates = (
        list(root.rglob(f"{app}-{source}-{suffix}.txt")) if root.exists() else []
    )
    return candidates[0] if candidates else None


def _render_patch_page(app: str, report: dict, config: list[dict]) -> str:
    default_source = str(report.get("source") or "")
    applied = [str(name) for name in report.get("applied_patches", [])]
    gboard_extra = _gboard_supplemental_selections(config)
    metadata_cache: dict[str, dict[str, dict]] = {}
    lines = [
        f"# {_label_app(app)} — 適用パッチ",
        "",
        "この一覧は設定値ではなく、ビルド時にCLIが `Applied:` と報告した結果を基準にしています。",
        "",
        f"**適用パッチ数:** {len(applied)}",
        "",
        "| Patch | Patch source | Source tag | Description |",
        "| --- | --- | --- | --- |",
    ]
    for patch in applied:
        source = _patch_source_for(app, default_source, patch, gboard_extra)
        metadata_cache.setdefault(source, _patch_entries(source))
        entry = metadata_cache[source].get(patch.casefold())
        url = _source_url(source)
        source_cell = (
            f"[{_label_source(source)}]({url})" if url else _label_source(source)
        )
        lines.append(
            f"| `{_safe(patch)}` | {source_cell} | `{_safe(_source_tag(source))}` | {_safe(_description(entry))} |"
        )
    excluded = report.get("feature_failures") or report.get("excluded_patches") or []
    if excluded:
        lines.extend(["", "## 未適用・除外", ""])
        for item in excluded:
            if isinstance(item, dict):
                lines.append(
                    f"- `{_safe(item.get('name'))}` — {_safe(item.get('reason'))}"
                )
    return "\n".join(lines) + "\n"


def _render_origin_page(app: str, origin: dict | None) -> str:
    lines = [f"# {_label_app(app)} — APK取得元", ""]
    if not origin:
        lines.append("このReleaseではAPK originメタデータを取得できませんでした。")
        return "\n".join(lines) + "\n"
    cached = bool(origin.get("cached"))
    lines.extend(
        [
            f"- **Version:** `{_safe(origin.get('version'))}`",
            f"- **Architecture:** `{_safe(origin.get('architecture'))}`",
            f"- **取得経路:** {'GitHub Base APK Cache から復元' if cached else 'Providerから直接取得'}",
            f"- **元Provider:** `{_safe(origin.get('provider_label') or origin.get('provider'))}`",
        ]
    )
    provider_url = str(origin.get("provider_url") or "")
    origin_url = str(origin.get("origin_url") or "")
    if provider_url:
        lines.append(f"- **Provider:** [公式/配布ページ]({provider_url})")
    if origin_url:
        lines.append(f"- **元リンク:** [取得元を開く]({origin_url})")
    if cached:
        lines.append(f"- **Cache tag:** `{_safe(origin.get('cache_tag'))}`")
        if origin.get("legacy_cache_origin_unknown"):
            lines.extend(
                [
                    "",
                    "> このAPKはorigin sidecar導入前の旧キャッシュです。元Providerを復元できないため、キャッシュ由来であることだけを表示しています。",
                ]
            )
    return "\n".join(lines) + "\n"


def _render_vt_page(app: str, results: list[dict], vt: dict) -> str:
    lines = [
        f"# {_label_app(app)} — VirusTotal",
        "",
        "**対象:** パッチ適用前に取得したBase APK（配布元から取得/キャッシュ復元した原本）",
        "",
    ]
    if not results:
        lines.append("このアプリに対応するVirusTotal結果を見つけられませんでした。")
        return "\n".join(lines) + "\n"
    clean = all(str(item.get("verdict")) == "clean" for item in results)
    lines.extend(
        [
            f"- **結果:** {'✅ Clean' if clean else '⚠️ Review required'}",
            f"- **スキャン対象:** {len(results)} file(s)",
            "",
            "| File | SHA-256 | Malicious | Suspicious | Method | VirusTotal |",
            "| --- | --- | ---: | ---: | --- | --- |",
        ]
    )
    for item in results:
        permalink = str(item.get("permalink") or "")
        vt_link = f"[Open]({permalink})" if permalink else "-"
        lines.append(
            f"| `{_safe(item.get('file'))}` | `{_safe(item.get('sha256'))}` | "
            f"{int(item.get('malicious') or 0)} | {int(item.get('suspicious') or 0)} | "
            f"`{_safe(item.get('method'))}` | {vt_link} |"
        )
        engines = item.get("engines") or {}
        detected = (
            [
                (name, detail)
                for name, detail in engines.items()
                if isinstance(detail, dict)
                and detail.get("category") in {"malicious", "suspicious"}
            ]
            if isinstance(engines, dict)
            else []
        )
        if detected:
            lines.extend(["", f"### {_safe(item.get('file'))} detections", ""])
            for name, detail in sorted(detected):
                lines.append(
                    f"- **{_safe(name)}**: `{_safe(detail.get('category'))}` — {_safe(detail.get('result'))}"
                )
    telemetry = vt.get("telemetry") if isinstance(vt, dict) else None
    if isinstance(telemetry, dict):
        lines.extend(
            [
                "",
                "## Scanner telemetry",
                "",
                f"- Persistent cache hits: `{_safe(telemetry.get('cache_hits'))}`",
                f"- New hashes: `{_safe(telemetry.get('new_hashes'))}`",
                f"- Analyses started: `{_safe(telemetry.get('analyses_started'))}`",
            ]
        )
    return "\n".join(lines) + "\n"


def _render_build_page(app: str, report: dict, run_url: str) -> str:
    source = str(report.get("source") or "")
    lines = [
        f"# {_label_app(app)} — ビルド詳細",
        "",
        f"- **Status:** `{_safe(report.get('lifecycle_status') or report.get('status'))}`",
        f"- **Version:** `{_safe(report.get('version'))}`",
        f"- **Patch source:** {_source_display(app, source)}",
        f"- **Applying count:** `{_safe(report.get('applying_count'))}`",
        f"- **Applied count:** `{len(report.get('applied_patches') or [])}`",
        f"- **Required satisfied:** `{_safe(report.get('required_patches_satisfied'))}`",
    ]
    if app == "gboard":
        lines.extend(
            [
                "",
                "## Gboard 3-source integration",
                "",
                "1つのAPKに **Jason + Adobo + Morning-Entree** の3ソースを統合しています。",
                "補助2ソースは明示allowlistだけを有効化し、Jasonと重複する機能はJason側を優先して抑止します。",
                "Release details公開前に、補助ソースの有効パッチが実際に `Applied:` と報告されたことも検証します。",
            ]
        )
    if run_url:
        lines.extend(["", f"[GitHub Actions run を開く]({run_url})"])
    return "\n".join(lines) + "\n"


def _render_developer_index(app: str, run_url: str) -> str:
    lines = [
        f"# {_label_app(app)} — Developer diagnostics",
        "",
        "通常利用者向けページから分離した、生のメタデータと診断用抜粋です。",
        "",
        "- [build-report.json](build-report.json)",
        "- [build-status.txt](build-status.txt)",
        "- [download-status.txt](download-status.txt)",
        "- [virustotal.json](virustotal.json)",
    ]
    if run_url:
        lines.extend(
            [
                "",
                f"- [GitHub Actions run（完全なjob logはこちら）]({run_url})",
                "",
                "> リポジトリには診断用メタデータ/サニタイズ済みstatus抜粋だけを蓄積し、完全なActionsログはGitHub Actions側で確認します。",
            ]
        )
    return "\n".join(lines) + "\n"


def _render_variant_index(
    app: str,
    report: dict,
    origin: dict | None,
    vt_results: list[dict],
) -> str:
    source = str(report.get("source") or "")
    cached = bool(origin and origin.get("cached"))
    vt_clean = bool(vt_results) and all(
        str(item.get("verdict")) == "clean" for item in vt_results
    )
    return "\n".join(
        [
            f"# {_label_app(app)} — {_source_display(app, source)}",
            "",
            f"- **Version:** `{_safe(report.get('version'))}`",
            f"- **Applied patches:** {len(report.get('applied_patches') or [])}",
            f"- **APK origin:** {_safe((origin or {}).get('provider_label') or (origin or {}).get('provider'))}{' (cache restore)' if cached else ''}",
            f"- **VirusTotal:** {'✅ Clean' if vt_clean else '詳細を確認'}",
            "",
            "## Details",
            "",
            "- [適用パッチとパッチ説明](patches.md)",
            "- [APK取得元・キャッシュ由来](apk-source.md)",
            "- [VirusTotal詳細](virustotal.md)",
            "- [ビルド詳細](build.md)",
            "- [開発者向け診断・ログ](developer/README.md)",
            "",
            "[← App index](../../README.md)",
        ]
    ) + "\n"


def _render_app_index(app: str, variants: list[dict]) -> str:
    lines = [
        f"# {_label_app(app)}",
        "",
        "このReleaseに含まれるビルド構成を選んでください。",
        "",
        f"**構成数:** {len(variants)}",
        "",
        "| Patch source / variant | Version | Applied patches |",
        "| --- | --- | ---: |",
    ]
    for report in variants:
        source = str(report.get("source") or "")
        lines.append(
            f"| [{_source_display(app, source)}](variants/{source}/README.md) | "
            f"`{_safe(report.get('version'))}` | {len(report.get('applied_patches') or [])} |"
        )
    lines.extend(["", "[← Release index](../../README.md)"])
    return "\n".join(lines) + "\n"


def _write_variant(
    app: str,
    report: dict,
    origins: list[dict],
    vt: dict,
    config: list[dict],
    apps_dir: Path,
    build_results: Path,
    download_results: Path,
    run_url: str,
) -> None:
    source = str(report.get("source") or "")
    origin = _find_origin(origins, app, source)
    vt_results = _find_vt(vt, app, source)
    variant_dir = apps_dir / app / "variants" / source
    dev_dir = variant_dir / "developer"
    dev_dir.mkdir(parents=True, exist_ok=True)

    (variant_dir / "README.md").write_text(
        _render_variant_index(app, report, origin, vt_results), encoding="utf-8"
    )
    (variant_dir / "patches.md").write_text(
        _render_patch_page(app, report, config), encoding="utf-8"
    )
    (variant_dir / "apk-source.md").write_text(
        _render_origin_page(app, origin), encoding="utf-8"
    )
    (variant_dir / "virustotal.md").write_text(
        _render_vt_page(app, vt_results, vt), encoding="utf-8"
    )
    (variant_dir / "build.md").write_text(
        _render_build_page(app, report, run_url), encoding="utf-8"
    )
    (dev_dir / "README.md").write_text(
        _render_developer_index(app, run_url), encoding="utf-8"
    )
    clean_report = {key: value for key, value in report.items() if key != "_path"}
    (dev_dir / "build-report.json").write_text(
        json.dumps(clean_report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (dev_dir / "virustotal.json").write_text(
        json.dumps({"results": vt_results}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    _copy_or_text(
        _matching_status_file(build_results, app, source, "build"),
        dev_dir / "build-status.txt",
        "Build status artifact was not available.\n",
    )
    _copy_or_text(
        _matching_status_file(download_results, app, source, "download"),
        dev_dir / "download-status.txt",
        "Download status artifact was not available.\n",
    )


def generate(args: argparse.Namespace) -> tuple[Path, Path]:
    if not args.tag:
        raise ValueError("release tag is required")

    reports = [
        report
        for report in _load_reports(args.build_results)
        if report.get("status") == "success"
    ]
    origins = _load_origins(args.base_inputs)
    vt = _load_vt(args.virustotal)
    config = _load_patch_config()

    by_app: dict[str, list[dict]] = defaultdict(list)
    for report in reports:
        by_app[str(report.get("app_name"))].append(report)
    for variants in by_app.values():
        variants.sort(key=lambda report: str(report.get("source") or ""))

    release_dir = args.output_root / args.tag
    apps_dir = release_dir / "apps"
    release_dir.mkdir(parents=True, exist_ok=True)

    variant_count = 0
    for app in sorted(by_app, key=lambda value: _label_app(value).casefold()):
        variants = by_app[app]
        app_dir = apps_dir / app
        app_dir.mkdir(parents=True, exist_ok=True)
        (app_dir / "README.md").write_text(
            _render_app_index(app, variants), encoding="utf-8"
        )
        for report in variants:
            _write_variant(
                app,
                report,
                origins,
                vt,
                config,
                apps_dir,
                args.build_results,
                args.download_results,
                args.run_url,
            )
            variant_count += 1

    index_lines = [
        f"# Release details — {args.tag}",
        "",
        f"- **収録アプリ数:** {len(by_app)}",
        f"- **ビルド構成数:** {variant_count}",
        f"- **Generated:** {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
    ]
    if args.run_url:
        index_lines.append(f"- **Build:** [GitHub Actions run]({args.run_url})")
    index_lines.extend(
        [
            "",
            "| App | Variants | Versions | Patch sources |",
            "| --- | ---: | --- | --- |",
        ]
    )
    for app in sorted(by_app, key=lambda value: _label_app(value).casefold()):
        variants = by_app[app]
        versions = ", ".join(
            f"`{_safe(report.get('version'))}`" for report in variants
        )
        sources = ", ".join(
            _source_display(app, str(report.get("source") or ""))
            for report in variants
        )
        index_lines.append(
            f"| [{_label_app(app)}](apps/{app}/README.md) | {len(variants)} | {versions} | {_safe(sources)} |"
        )
    (release_dir / "README.md").write_text(
        "\n".join(index_lines) + "\n", encoding="utf-8"
    )

    release_index_url = _repo_url(
        args.repository, f"release-details/{args.tag}/README.md"
    )
    notes = [
        "## Included apps",
        "",
        f"**収録アプリ数: {len(by_app)}**",
        "",
        "| App | Version(s) | Patch source(s) |",
        "| --- | --- | --- |",
    ]
    for app in sorted(by_app, key=lambda value: _label_app(value).casefold()):
        variants = by_app[app]
        versions = ", ".join(
            f"`{_safe(report.get('version'))}`" for report in variants
        )
        sources = ", ".join(
            _source_display(app, str(report.get("source") or ""))
            for report in variants
        )
        app_url = _repo_url(
            args.repository,
            f"release-details/{args.tag}/apps/{app}/README.md",
        )
        notes.append(
            f"| [{_label_app(app)}]({app_url}) | {versions} | {_safe(sources)} |"
        )
    notes.extend(["", f"[Release details / 詳細]({release_index_url})"])
    args.release_notes.write_text("\n".join(notes) + "\n", encoding="utf-8")
    return release_dir, args.release_notes


def main() -> None:
    args = _args()
    release_dir, notes = generate(args)
    print(f"Release details generated: {release_dir}")
    print(f"Release notes generated: {notes}")


if __name__ == "__main__":
    main()
