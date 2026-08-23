"""Generate user-facing notes for APKs actually included in a release.

Source -> apps is derived from my-patch-config.json using the same selection
inputs as scripts/prepare_matrix.py. In CI, successful artifact directories are
authoritative so failed jobs and their internal diagnostics never appear in the
public release notes.
"""

from __future__ import annotations

import json
import os
from collections import defaultdict
from pathlib import Path


MAX_RELEASE_NOTES_LENGTH = 120_000

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
    "shaun-the-sheep-patches": "shaun-the-sheep-patches",
    "hxreborn": "hxreborn",
    "nekogryphou": "NekoGryphou",
}

APP_LABELS = {
    "1-1-1-1": "1.1.1.1",
    "accubattery": "AccuBattery",
    "adguard": "AdGuard",
    "adobe-acrobat": "Adobe Acrobat",
    "adobe-scan": "Adobe Scan",
    "alarmy": "Alarmy",
    "aliexpress": "AliExpress",
    "amazon-music": "Amazon Music",
    "amazon-shopping": "Amazon Shopping",
    "brave": "Brave",
    "brave-beta": "Brave Beta",
    "brave-nightly": "Brave Nightly",
    "call-recorder": "Call Recorder",
    "camscanner": "CamScanner",
    "countdown-widget": "Countdown Widget",
    "crunchyroll": "Crunchyroll",
    "disney-plus": "Disney+",
    "duolingo": "Duolingo",
    "excel": "Excel",
    "file-manager": "File Manager",
    "fing": "Fing",
    "foldersync": "FolderSync",
    "gboard": "Gboard",
    "github": "GitHub",
    "google-news": "Google News",
    "google-photos": "Google Photos",
    "google-recorder": "Google Recorder",
    "ibs_paint": "ibis Paint X",
    "icon-packer": "Icon Packer",
    "ilovepdf": "iLovePDF",
    "inshot": "InShot",
    "kahoot": "Kahoot!",
    "kinemaster": "KineMaster",
    "kinestop": "KineStop",
    "lightroom": "Lightroom Mobile",
    "line": "LINE",
    "mega": "MEGA",
    "netflix-ninja": "Netflix",
    "ninja-vpn": "Ninja VPN",
    "nova": "Nova Launcher",
    "photomath": "Photomath",
    "photoshop-mix": "Adobe Photoshop Mix",
    "pixiv": "Pixiv",
    "poweramp": "Poweramp",
    "prime-video": "Prime Video",
    "prime-video-android-tv": "Prime Video (Android TV)",
    "proton-mail": "Proton Mail",
    "proton-vpn": "Proton VPN",
    "sd-maid-se": "SD Maid SE",
    "sleep-as-android": "Sleep as Android",
    "smart_launcher": "Smart Launcher",
    "soundcloud": "SoundCloud",
    "speedtest": "Speedtest",
    "threads": "Threads",
    "tiktok": "TikTok",
    "tumblr": "Tumblr",
    "twitch": "Twitch",
    "twitch-android-tv": "Twitch (Android TV)",
    "viber": "Viber",
    "windscribe-vpn": "Windscribe VPN",
    "windy": "Windy",
    "word": "Word",
    "wps-office": "WPS Office",
    "xodo": "Xodo",
    "xrecorder": "XRecorder",
    "youtube": "YouTube",
    "youtube-music": "YouTube Music",
    "yuucho-ninsho": "Yuucho Ninsho",
    "yuucho-tsucho": "Yuucho Tsucho",
}

# Legacy env names still supplied by build.yml. They are used only as an
# additional signal/fresh-tag override; app grouping never comes from them.
LEGACY_ENV_KEYS = {
    "morphe": "MORPHE",
    "revanced-anddea": "ANDDEA",
    "rushiranpise": "RUSHIRANPISE",
    "rookie": "ROOKIE",
}


def _truthy(value: object) -> bool:
    return str(value or "").strip().casefold() == "true"


def _csv(value: object) -> set[str]:
    return {
        part.strip()
        for part in str(value or "").split(",")
        if part.strip()
    }


def _load_event_inputs() -> dict[str, object]:
    path_value = os.environ.get("GITHUB_EVENT_PATH", "").strip()
    if not path_value:
        return {}
    try:
        payload = json.loads(Path(path_value).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    inputs = payload.get("inputs")
    return inputs if isinstance(inputs, dict) else {}


def _successful_release_matrix() -> list[dict] | None:
    """Return successful CI matrix entries, or None outside release CI."""
    raw = os.environ.get("EXPECTED_MATRIX", "").strip()
    if not raw:
        return None
    expected = json.loads(raw)
    if not isinstance(expected, list):
        raise ValueError("EXPECTED_MATRIX must be a JSON array")

    artifact_root = Path(os.environ.get("ARTIFACT_ROOT", "all-apks"))
    successful: list[dict] = []
    for item in expected:
        if (
            not isinstance(item, dict)
            or not item.get("app_name")
            or not item.get("source")
        ):
            continue
        artifact = artifact_root / f"apk-{item['app_name']}-{item['source']}"
        if artifact.is_dir() and any(artifact.rglob("*.apk")):
            successful.append(item)
    return successful


def _load_patch_config(path: Path | None = None) -> list[dict]:
    config_path = path or Path(
        os.environ.get("PATCH_CONFIG_PATH", "my-patch-config.json")
    )
    data = json.loads(config_path.read_text(encoding="utf-8"))
    items = data.get("patch_list", [])
    if not isinstance(items, list):
        raise ValueError("my-patch-config.json: patch_list must be an array")
    return [
        item
        for item in items
        if isinstance(item, dict)
        and item.get("enabled", True) is not False
        and item.get("skip_build", False) is not True
        and item.get("app_name")
        and item.get("source")
    ]


def _source_url(source: str) -> str:
    """Build the upstream patch-source URL from sources/<source>.json."""
    path = Path("sources") / f"{source}.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""

    if not isinstance(data, list):
        return ""

    # The source files contain a name record, Morphe CLI record, and then the
    # actual patch bundle record. Pick the last repository record.
    repo_record = next(
        (
            item
            for item in reversed(data)
            if isinstance(item, dict) and item.get("user") and item.get("repo")
        ),
        None,
    )
    if not repo_record:
        return ""

    user = str(repo_record["user"])
    repo = str(repo_record["repo"])
    if repo_record.get("gitlab"):
        return f"https://gitlab.com/{user}/{repo}"
    return f"https://github.com/{user}/{repo}"


def _legacy_flag(source: str, suffix: str) -> bool:
    env_key = LEGACY_ENV_KEYS.get(source)
    if not env_key:
        return False
    return _truthy(os.environ.get(f"{env_key}_{suffix}"))


def _requested_matrix(items: list[dict], inputs: dict[str, object]) -> list[dict]:
    """Mirror the high-level selection modes in scripts/prepare_matrix.py."""
    build_all = _truthy(inputs.get("build_all_sources"))
    updated_sources = _csv(inputs.get("updated_sources"))
    if "anddea" in updated_sources:
        updated_sources.add("revanced-anddea")

    updated_apps = _csv(inputs.get("updated_apps"))

    if build_all:
        return list(items)
    if updated_sources:
        return [
            item for item in items
            if str(item["source"]) in updated_sources
        ]
    if updated_apps:
        return [
            item for item in items
            if str(item["app_name"]) in updated_apps
        ]

    # Backward-compatible workflow_dispatch path.
    legacy_sources = {
        source
        for source in LEGACY_ENV_KEYS
        if _legacy_flag(source, "UPDATED") or _legacy_flag(source, "FORCE")
    }
    if legacy_sources:
        apk_updated_apps: set[str] = set()
        raw = inputs.get("apk_updated_apps", "[]")
        try:
            parsed = json.loads(str(raw))
            if isinstance(parsed, list):
                apk_updated_apps = {str(app) for app in parsed}
        except json.JSONDecodeError:
            pass

        selected: list[dict] = []
        for item in items:
            source = str(item["source"])
            app = str(item["app_name"])
            if source not in legacy_sources:
                continue
            if _legacy_flag(source, "FORCE") and not _legacy_flag(source, "UPDATED"):
                if apk_updated_apps and app not in apk_updated_apps:
                    continue
            selected.append(item)
        if selected:
            return selected

    # If release_notes.py is run manually without the Actions event context,
    # showing the configured matrix is safer than producing a misleading empty
    # source table.
    return list(items)


def _source_cell(source: str, url: str) -> str:
    label = SOURCE_LABELS.get(source, source)
    return f"[{label}]({url})" if url else label


def _readme_url() -> str:
    repository = os.environ.get(
        "GITHUB_REPOSITORY", "almeki876/Morphe-AutoBuilds"
    ).strip()
    return f"https://github.com/{repository}#readme"


def render() -> str:
    inputs = _load_event_inputs()
    successful = _successful_release_matrix()
    selected = (
        successful
        if successful is not None
        else _requested_matrix(_load_patch_config(), inputs)
    )

    grouped: dict[str, list[str]] = defaultdict(list)
    seen: set[tuple[str, str]] = set()
    for item in selected:
        app = str(item["app_name"])
        source = str(item["source"])
        pair = (source, app)
        if pair in seen:
            continue
        seen.add(pair)
        grouped[source].append(app)

    lines = [
        "## Included APKs",
        "",
        "| Apps | Patch source |",
        "| --- | --- |",
    ]

    for source in sorted(
        grouped,
        key=lambda key: SOURCE_LABELS.get(key, key).casefold(),
    ):
        apps = sorted(
            grouped[source],
            key=lambda app: APP_LABELS.get(app, app).casefold(),
        )
        url = _source_url(source)
        apps_text = ", ".join(APP_LABELS.get(app, app) for app in apps)
        lines.append(f"| {apps_text} | {_source_cell(source, url)} |")

    lines.extend(["", f"[Details / 詳細]({_readme_url()})"])
    return "\n".join(lines) + "\n"


def _fit_release_notes(text: str) -> str:
    if len(text) <= MAX_RELEASE_NOTES_LENGTH:
        return text
    suffix = (
        "\n\n"
        "[Additional release details omitted to fit GitHub's release-notes limit.]"
        "\n"
    )
    return text[: MAX_RELEASE_NOTES_LENGTH - len(suffix)] + suffix


def main() -> None:
    output = Path(os.environ.get("RELEASE_NOTES_PATH", "release_notes.md"))
    # Internal build diagnostics and the successful VirusTotal report remain in
    # the Actions job summary. Unsafe or inconclusive scans block the release,
    # so neither report belongs in the public, user-facing release notes.
    output.write_text(_fit_release_notes(render()), encoding="utf-8")
    print(f"Release notes generated: {output}")


if __name__ == "__main__":
    main()
