"""Generate a compact Markdown catalog of the newest APK for every build target."""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote


ASSET_RE = re.compile(
    r"^(?P<app>.+?)-(?P<arch>universal|arm64-v8a|armeabi-v7a|x86_64|x86)-"
    r"(?P<source>.+)-v(?P<version>.+)\.apk$",
    re.IGNORECASE,
)

RELEASE_BASE_URL = "https://github.com/almeki876/Morphe-AutoBuilds/releases/tag"
JST = timezone(timedelta(hours=9), name="JST")

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

ARCH_ORDER = {
    "universal": 0,
    "arm64-v8a": 1,
    "armeabi-v7a": 2,
    "x86_64": 3,
    "x86": 4,
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


def configured_target_order(
    config_path: Path = Path("my-patch-config.json"),
) -> list[tuple[str, str]]:
    """Return app/source identities in the same order used by the Obtainium catalog."""
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    entries = raw.get("patch_list") if isinstance(raw, dict) else None
    if not isinstance(entries, list):
        return []

    ordered: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in entries:
        if not isinstance(item, dict) or not item.get("app_name") or not item.get("source"):
            continue
        target = (str(item["app_name"]), str(item["source"]))
        if target not in seen:
            seen.add(target)
            ordered.append(target)
    return ordered


def configured_targets(config_path: Path = Path("my-patch-config.json")) -> set[tuple[str, str]]:
    """Return the current app/source identities that are allowed in the catalog."""
    return set(configured_target_order(config_path))


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


def _format_release_timestamp(release: dict) -> str:
    timestamp = _release_timestamp(release)
    if not timestamp:
        return "不明"
    try:
        parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError:
        return timestamp
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(JST).strftime("%Y-%m-%d %H:%M JST")


def newest_assets(
    payload: object,
    *,
    targets: set[tuple[str, str]] | None = None,
) -> tuple[list[ApkAsset], list[dict], list[dict]]:
    """Return the newest known asset for each current app/source/arch identity."""
    releases = [release for release in _flatten_releases(payload) if not release.get("draft")]
    releases.sort(key=_release_timestamp, reverse=True)

    parsed_by_key: dict[tuple[str, str, str], ApkAsset] = {}
    unmatched_by_name: dict[str, dict] = {}
    for release_index, release in enumerate(releases):
        tag = str(release.get("tag_name") or "latest")
        for asset in release.get("assets") or []:
            if not isinstance(asset, dict):
                continue
            name = str(asset.get("name") or "")
            if not name.lower().endswith(".apk"):
                continue
            item = parse_asset(asset, release_tag=tag)
            if item is None or (targets and (item.app, item.source) not in targets):
                if release_index == 0:
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


def render(
    payload: object,
    *,
    source_root: Path = Path("sources"),
    config_path: Path = Path("my-patch-config.json"),
) -> str:
    target_order = configured_target_order(config_path)
    targets = set(target_order)
    parsed, _unmatched, releases = newest_assets(payload, targets=targets or None)
    metadata = source_metadata(source_root)

    lines = ["# Direct APK Download Links", ""]
    if releases:
        latest_release = releases[0]
        release_tag = str(latest_release.get("tag_name") or "")
        if release_tag:
            release_url = str(latest_release.get("html_url") or "")
            if not release_url:
                release_url = f"{RELEASE_BASE_URL}/{quote(release_tag, safe='')}"
            lines.append(f"- 参照Release: [{release_tag}]({release_url})")
        else:
            lines.append("- 参照Release: 不明")
        lines.append(f"- 最終更新日時: {_format_release_timestamp(latest_release)}")
        lines.append("")

    grouped: dict[str, dict[str, list[ApkAsset]]] = {}
    for item in parsed:
        grouped.setdefault(item.source, {}).setdefault(item.app, []).append(item)

    source_order: list[str] = []
    app_order: dict[str, list[str]] = {}
    for app, source in target_order:
        if source not in source_order:
            source_order.append(source)
        if app not in app_order.setdefault(source, []):
            app_order[source].append(app)

    for source in grouped:
        if source not in source_order:
            source_order.append(source)
            app_order[source] = list(grouped[source])

    for source in source_order:
        if source not in grouped:
            continue
        label, source_url = metadata.get(source, (SOURCE_LABELS.get(source, source), None))
        lines.append(f"## [{label}]({source_url})" if source_url else f"## {label}")
        lines.append("")
        ordered_apps = [app for app in app_order.get(source, []) if app in grouped[source]]
        ordered_apps.extend(app for app in grouped[source] if app not in ordered_apps)
        for app in ordered_apps:
            assets = sorted(
                grouped[source][app],
                key=lambda item: (ARCH_ORDER.get(item.arch, 99), item.arch),
            )
            lines.extend([f"### {app_label(app)}", ""])
            links = [f"[{item.arch}]({item.url})" for item in assets]
            lines.extend([" · ".join(links), ""])

    if not parsed:
        lines.extend(["現在ダウンロードできるAPKはありません。", ""])

    return "\n".join(lines).rstrip() + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("release_json", nargs="?", default="release-history.json")
    parser.add_argument("--output", default="Morphe-AutoBuilds-Direct-Download.md")
    parser.add_argument("--config", default="my-patch-config.json")
    args = parser.parse_args()
    payload = json.loads(Path(args.release_json).read_text(encoding="utf-8"))
    Path(args.output).write_text(
        render(payload, config_path=Path(args.config)),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
