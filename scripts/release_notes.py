"""Generate user-facing notes for APKs actually included in a release.

Source -> apps is derived from my-patch-config.json using the same selection
inputs as scripts/prepare_matrix.py. In CI, successful artifact directories are
authoritative so failed jobs and their internal diagnostics never appear in the
public release notes.

When this module runs inside the create-release job, it also generates and
commits the persistent release-details tree *before* the GitHub Release is
created.  That makes every app/detail link valid from the first moment the
Release is published instead of relying on a second workflow to edit notes
later.
"""

from __future__ import annotations

import json
import os
import subprocess
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo


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


def _requested_matrix(items: list[dict], inputs: dict[str, object]) -> list[dict]:
    """Mirror the high-level selection modes in scripts/prepare_matrix.py."""
    build_all = _truthy(inputs.get("build_all_sources"))
    updated_sources = _csv(inputs.get("updated_sources"))
    updated_apps = _csv(inputs.get("updated_apps"))

    if build_all:
        return list(items)
    if updated_sources or updated_apps:
        return [
            item for item in items
            if (
                str(item["source"]) in updated_sources
                or str(item["app_name"]) in updated_apps
            )
        ]

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


def _release_ci_inputs_available() -> bool:
    return (
        _truthy(os.environ.get("GITHUB_ACTIONS"))
        and Path("all-apks").is_dir()
        and Path("build-results").is_dir()
        and Path("virustotal_base_results.json").is_file()
    )


def _release_tag_from_previous_step() -> str:
    """Resolve the tag produced by the immediately preceding Actions step.

    build.yml writes ``release_tag=...`` to that step's GITHUB_OUTPUT.  Runner
    command files normally remain beside the current step's command file for
    the duration of the job, so inspect them first.  RELEASE_TAG remains an
    explicit override for tests/manual callers.  The JST clock is only a final
    fallback and uses the exact same format as build.yml.
    """
    explicit = os.environ.get("RELEASE_TAG", "").strip()
    if explicit:
        return explicit

    output_file = os.environ.get("GITHUB_OUTPUT", "").strip()
    if output_file:
        parent = Path(output_file).parent
        try:
            candidates = sorted(parent.glob("set_output_*"), key=lambda p: p.stat().st_mtime, reverse=True)
        except OSError:
            candidates = []
        for candidate in candidates:
            try:
                for line in candidate.read_text(encoding="utf-8", errors="replace").splitlines():
                    if line.startswith("release_tag="):
                        value = line.split("=", 1)[1].strip()
                        if value:
                            return value
            except OSError:
                continue

    return datetime.now(ZoneInfo("Asia/Tokyo")).strftime("%Y-%m-%d_%H-%M-JST")


def _temporarily_overlay_source_tags() -> bytes | None:
    """Make release detail source tags match the tags resolved in this run."""
    path = Path("last-tags.json")
    try:
        original = path.read_bytes()
        data = json.loads(original.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return original

    try:
        mappings = json.loads(os.environ.get("SOURCE_TAGS_JSON", "{}"))
    except json.JSONDecodeError:
        mappings = {}
    if not isinstance(mappings, dict):
        mappings = {}
    changed = False
    for key, raw_value in mappings.items():
        value = str(raw_value).strip()
        if not value:
            continue
        if data.get(key) != value:
            data[key] = value
            changed = True
    if changed:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return original


def _restore_source_tags(original: bytes | None) -> None:
    if original is not None:
        Path("last-tags.json").write_bytes(original)


def _commit_release_details(release_dir: Path, release_tag: str) -> None:
    """Commit detail pages before ``gh release create`` consumes the notes."""
    subprocess.run(["git", "config", "user.name", "github-actions[bot]"], check=True)
    subprocess.run(
        [
            "git",
            "config",
            "user.email",
            "41898282+github-actions[bot]@users.noreply.github.com",
        ],
        check=True,
    )
    subprocess.run(["git", "add", "--", str(release_dir)], check=True)
    diff = subprocess.run(["git", "diff", "--cached", "--quiet"], check=False)
    if diff.returncode == 0:
        return
    if diff.returncode != 1:
        raise subprocess.CalledProcessError(diff.returncode, diff.args)

    subprocess.run(
        ["git", "commit", "-m", f"docs: archive build details for {release_tag} [skip ci]"],
        check=True,
    )
    subprocess.run(["git", "pull", "--rebase", "origin", "main"], check=True)
    subprocess.run(["git", "push", "origin", "HEAD:main"], check=True)


def _generate_and_publish_release_details(output: Path) -> bool:
    if not _release_ci_inputs_available():
        return False

    # Import lazily: generate_release_details imports APP_LABELS/SOURCE_LABELS
    # from this module, so importing it at module import time would be circular.
    from scripts import generate_release_details

    release_tag = _release_tag_from_previous_step()
    repository = os.environ.get(
        "GITHUB_REPOSITORY", "almeki876/Morphe-AutoBuilds"
    ).strip()
    server = os.environ.get("GITHUB_SERVER_URL", "https://github.com").rstrip("/")
    run_id = os.environ.get("GITHUB_RUN_ID", "").strip()
    run_url = f"{server}/{repository}/actions/runs/{run_id}" if run_id else ""

    args = SimpleNamespace(
        tag=release_tag,
        repository=repository,
        run_url=run_url,
        build_results=Path("build-results"),
        # create-release does not separately download download-status artifacts;
        # build diagnostics remain available through the Actions run link.
        download_results=Path("download-results"),
        # Every successful apk-* artifact already contains
        # build-metadata/apk-sources.json, so provenance can be reconstructed
        # without a second artifact-download phase.
        base_inputs=Path("all-apks"),
        virustotal=Path("virustotal_base_results.json"),
        output_root=Path("release-details"),
        release_notes=output,
    )

    original_tags = _temporarily_overlay_source_tags()
    try:
        release_dir, notes = generate_release_details.generate(args)
    finally:
        _restore_source_tags(original_tags)

    # Fit after the linked notes have been generated.
    notes.write_text(_fit_release_notes(notes.read_text(encoding="utf-8")), encoding="utf-8")
    _commit_release_details(release_dir, release_tag)
    print(f"Release details committed before Release publication: {release_dir}")
    return True


def main() -> None:
    output = Path(os.environ.get("RELEASE_NOTES_PATH", "release_notes.md"))

    if _generate_and_publish_release_details(output):
        print(f"Linked release notes generated: {output}")
        return

    # Local/tests/fallback behavior stays compatible with the previous notes
    # generator when release artifacts are not present.
    output.write_text(_fit_release_notes(render()), encoding="utf-8")
    print(f"Release notes generated: {output}")


if __name__ == "__main__":
    main()
