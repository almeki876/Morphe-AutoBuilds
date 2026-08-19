"""Persist upstream state only after the complete build and release succeeds."""

from __future__ import annotations

import json
import os
from pathlib import Path
import runpy
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


STATE_FILE = Path("last-tags.json")
SOURCE_ENV = {
    "morphe": "SOURCE_TAG_MORPHE",
    "anddea": "SOURCE_TAG_ANDDEA",
    "rushiranpise": "SOURCE_TAG_RUSHIRANPISE",
    "rookie": "SOURCE_TAG_ROOKIE",
    "tosox": "SOURCE_TAG_TOSOX",
    "yuzu": "SOURCE_TAG_YUZU",
    "dropped": "SOURCE_TAG_DROPPED",
}


def latest_github_tag(owner: str, repo: str) -> str:
    request = Request(
        f"https://api.github.com/repos/{owner}/{repo}/releases?per_page=100",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {os.environ.get('GITHUB_TOKEN', '')}",
            "User-Agent": "Morphe-AutoBuilds",
        },
    )
    with urlopen(request, timeout=30) as response:
        releases = json.load(response)
    releases = [release for release in releases if release.get("published_at")]
    releases.sort(key=lambda release: release["published_at"], reverse=True)
    return releases[0]["tag_name"] if releases else ""


def main() -> None:
    try:
        state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        state = {}
    if not isinstance(state, dict):
        state = {}

    for key, env_name in SOURCE_ENV.items():
        value = os.getenv(env_name, "").strip()
        if not value and key == "anddea":
            value = os.getenv("SOURCE_TAG_REVANCED_ANDDEA", "").strip()
        if value and value not in {"latest", "unknown"} and not value.startswith("{"):
            state[key] = value

    sources_dir = Path("sources")
    for source_path in sources_dir.glob("*.json"):
        repositories = json.loads(source_path.read_text(encoding="utf-8"))
        if not isinstance(repositories, list) or len(repositories) < 3:
            continue
        repository = repositories[2]
        if repository.get("gitlab"):
            continue
        try:
            value = latest_github_tag(repository["user"], repository["repo"])
        except (HTTPError, URLError, TimeoutError, KeyError, IndexError):
            continue
        if value:
            state[repositories[0]["name"]] = value

    STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    # The existing script resolves every current APK version while preserving
    # prior values for providers that are temporarily unavailable.
    runpy.run_path("scripts/save_apk_versions.py", run_name="__main__")


if __name__ == "__main__":
    main()
