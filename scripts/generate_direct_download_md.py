"""Generate a Markdown catalog of the newest APK available for every build target."""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


ASSET_RE = re.compile(
    r"^(?P<app>.+?)-(?P<arch>universal|arm64-v8a|armeabi-v7a|x86_64|x86)-"
    r"(?P<source>.+)-v(?P<version>.+)\.apk$",
    re.IGNORECASE,
)

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

APP_LABELS = {
    "youtube": "YouTube",
    "youtube-music": "YouTube Music",
    "google-photos": "Google Photos",
    "google-news": "Google News",
    "google-maps": "Google Maps",
    "reddit": "Reddit",
    "spotify": "Spotify",
    "instagram": "Instagram",
    "tiktok": "TikTok",
    "twitter": "X (Twitter)",
}


@dataclass(frozen=True)
class ApkAsset:
    app: str
    arch: str
    source: str
    version: str
    name: str
    url: str
    release_tag: str = "latest"


def parse_asset(asset: dict, *, release_tag: str = "latest") -> ApkAsset | None:
    name = str(asset.get("name") or "")
    url = str(asset.get("browser_download_url") or "")
    if not name.lower().endswith(".apk") or not url:
        return None
    match = ASSET_RE.match(name)
    if not match:
        return None
    return ApkAsset(name=name, url=url, release_tag=release_tag, **match.groupdict())


def _flatten_releases(payload: object) -> list[dict]:
    """Accept one release, a release list, or gh --paginate --slurp output."""
    if isinstance(payload, dict):
        return [payload]
    if not isinstance(payload, list):
        return []
    releases: list[dict] = []
    for item in payload:
        if isinstance(item, dict):
            releases.append(item)
        elif isinstance(item, list):
            releases.extend(value for value in item if isinstance(value, dict))
    return releases


def _release_timestamp(release: dict) -> str:
    return str(release.get("published_at") or release.get("created_at") or "")


def newest_assets(payload: object) -> tuple[list[ApkAsset], list[dict], list[dict]]:
    """Return the newest known asset for each app/source/architecture identity.

    Build and Release APKs intentionally supports partial releases. Therefore a
    catalog generated from only releases/latest would drop every unaffected app
    whenever a small incremental build becomes the newest release. Walk release
    history newest-first and keep the first asset for each stable build target.
    """
    releases = [release for release in _flatten_releases(payload) if not release.get("draft")]
    releases.sort(key=_release_timestamp, reverse=True)

    parsed_by_key: dict[tuple[str, str, str], ApkAsset] = {}
    unmatched_by_name: dict[str, dict] = {}
    for release in releases:
        tag = str(release.get("tag_name") or "latest")
        for asset in release.get("assets") or []:
            if not isinstance(asset, dict):
                continue
            name = str(asset.get("name") or "")
            if not name.lower().endswith(".apk"):
                continue
            item = parse_asset(asset, release_tag=tag)
            if item is None:
                unmatched_by_name.setdefault(name, asset)
                continue
            parsed_by_key.setdefault((item.app, item.source, item.arch), item)

    return list(parsed_by_key.values()), list(unmatched_by_name.values()), releases


def source_metadata(root: Path = Path("sources")) -> dict[str, tuple[str, str | None]]:
    metadata: dict[str, tuple[str, str | None]] = {}
    if not root.is_dir():
        return metadata
    for path in sorted(root.glob("*.json")):
        try:
            entries = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(entries, list) or not entries:
            continue
        first = entries[0] if isinstance(entries[0], dict) else {}
        output_name = str(first.get("name") or path.stem)
        repositories = [
            item for item in entries[1:]
            if isinstance(item, dict) and item.get("user") and item.get("repo")
        ]
        preferred = next(
            (item for item in repositories if "patch" in str(item["repo"]).lower()),
            repositories[-1] if repositories else None,
        )
        url = (
            f"https://github.com/{preferred['user']}/{preferred['repo']}"
            if preferred else None
        )
        metadata[output_name] = (SOURCE_LABELS.get(path.stem, output_name), url)
    return metadata


def app_label(slug: str) -> str:
    if slug in APP_LABELS:
        return APP_LABELS[slug]
    return " ".join(part.upper() if len(part) <= 2 else part.capitalize() for part in slug.split("-"))


def render(payload: object, *, source_root: Path = Path("sources")) -> str:
    parsed, unmatched, releases = newest_assets(payload)
    metadata = source_metadata(source_root)

    newest_release = releases[0] if releases else {}
    published = _release_timestamp(newest_release)
    updated = published[:10] if published else datetime.now(timezone.utc).date().isoformat()
    tag = str(newest_release.get("tag_name") or "latest")
    release_url = str(
        newest_release.get("html_url")
        or "https://github.com/almeki876/Morphe-AutoBuilds/releases/latest"
    )

    lines = [
        "# Direct APK Download Links",
        "",
        f"最終更新: {updated} | 掲載APK数: {len(parsed) + len(unmatched)} | 最新Release: `{tag}`",
        "",
        "Obtainium / ObtainX 用とは別の、**APKを直接ダウンロードするための一覧**です。",
        "各リンクは、そのアプリ・パッチソース・アーキテクチャについて現在入手できる最新の GitHub Release asset を直接指します。",
        "部分リリースで更新対象にならなかったアプリは、直前の有効なリリースへのリンクを保持します。",
        "新しい `Build and Release APKs` が正常完了すると、このファイルも自動更新されます。",
        "",
        f"リリース全体: [最新リリースを開く]({release_url})",
        "",
    ]

    grouped: dict[str, dict[str, list[ApkAsset]]] = {}
    for item in parsed:
        grouped.setdefault(item.source, {}).setdefault(item.app, []).append(item)

    for source in sorted(grouped, key=lambda value: (metadata.get(value, (value, None))[0].casefold(), value)):
        label, source_url = metadata.get(source, (SOURCE_LABELS.get(source, source), None))
        lines.append(f"## [{label}]({source_url})" if source_url else f"## {label}")
        lines.append("")
        for app in sorted(grouped[source], key=lambda value: app_label(value).casefold()):
            assets = sorted(grouped[source][app], key=lambda item: (item.arch != "universal", item.arch, item.version))
            versions = ", ".join(dict.fromkeys(item.version for item in assets))
            lines.extend([f"### {app_label(app)} ({label})", "", f"Version: `{versions}`", ""])
            for item in assets:
                lines.append(f"- [⬇️ {item.arch} — {item.name}]({item.url})")
            lines.append("")

    if unmatched:
        lines.extend(["## Other APK assets", "", "命名規則に一致しないAPKです。削除せず、確認できる最新の直リンクを掲載します。", ""])
        for asset in sorted(unmatched, key=lambda item: str(item.get("name") or "")):
            name = str(asset.get("name") or "APK")
            url = str(asset.get("browser_download_url") or "")
            if url:
                lines.append(f"- [⬇️ {name}]({url})")
        lines.append("")

    if not parsed and not unmatched:
        lines.extend(["参照したリリースには APK asset がありません。", ""])

    return "\n".join(lines).rstrip() + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("release_json", nargs="?", default="release-history.json")
    parser.add_argument("--output", default="Morphe-AutoBuilds-Direct-Download.md")
    args = parser.parse_args()
    payload = json.loads(Path(args.release_json).read_text(encoding="utf-8"))
    Path(args.output).write_text(render(payload), encoding="utf-8")


if __name__ == "__main__":
    main()
