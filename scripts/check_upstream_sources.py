"""Check every patch source declared in sources/*.json for a new release."""

from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parent.parent
SOURCES_DIR = ROOT / "sources"
STATE_FILE = ROOT / "last-tags.json"


def latest_tag(owner: str, repo: str) -> str:
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


def main() -> int:
    try:
        state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        state = {}
    if not isinstance(state, dict):
        state = {}

    updated_sources: list[str] = []
    for source_path in sorted(SOURCES_DIR.glob("*.json")):
        repositories = json.loads(source_path.read_text(encoding="utf-8"))
        if not isinstance(repositories, list) or len(repositories) < 3:
            continue
        source = repositories[0]["name"]
        patch_repository = repositories[2]
        try:
            current = latest_tag(patch_repository["user"], patch_repository["repo"])
        except (HTTPError, URLError, TimeoutError, KeyError, IndexError) as error:
            print(f"WARNING: {source}: could not fetch release: {error}")
            continue

        previous = state.get(source, "")
        if previous and current and current != previous:
            workflow_source = "anddea" if source == "revanced-anddea" else source
            updated_sources.append(workflow_source)
            print(f"UPDATED: {source}: {previous} -> {current}")
        elif current:
            print(f"UNCHANGED: {source}: {current}")
        else:
            print(f"WARNING: {source}: no published release found")

    output_file = os.environ.get("GITHUB_OUTPUT")
    if output_file:
        with open(output_file, "a", encoding="utf-8") as output:
            output.write(f"any_updated={'true' if updated_sources else 'false'}\n")
            output.write(f"updated_sources={','.join(sorted(updated_sources))}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
