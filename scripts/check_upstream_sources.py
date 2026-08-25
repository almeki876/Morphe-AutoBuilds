"""Check every patch source declared in sources/*.json for a new release.

GitHub Releases are authoritative and prereleases are included. Tag-only refs
are deliberately ignored because the build requires Release assets. A source
whose configured tag is not ``latest`` is an explicit pin and is not monitored.
"""

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


def iter_sources() -> list[dict]:
    sources = []
    for source_path in sorted(SOURCES_DIR.glob("*.json")):
        repositories = json.loads(source_path.read_text(encoding="utf-8"))
        if not isinstance(repositories, list) or len(repositories) < 3:
            continue
        patch = repositories[2]
        sources.append({
            "name": str(repositories[0].get("name") or source_path.stem),
            "owner": str(patch["user"]),
            "repo": str(patch["repo"]),
            "ref": str(patch.get("tag") or "latest"),
            "gitlab": bool(patch.get("gitlab")),
        })
    return sources


def resolve_source_tag(source: dict) -> str:
    if source["ref"] != "latest":
        return source["ref"]
    if source["gitlab"]:
        raise RuntimeError("GitLab latest-release monitoring is not configured")
    return latest_tag(source["owner"], source["repo"])


def main() -> int:
    try:
        state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        state = {}
    if not isinstance(state, dict):
        state = {}

    updated_sources: list[str] = []
    source_tags: dict[str, str] = {}
    for source_spec in iter_sources():
        source = source_spec["name"]
        try:
            current = resolve_source_tag(source_spec)
        except (HTTPError, URLError, TimeoutError, KeyError, IndexError, RuntimeError) as error:
            print(f"WARNING: {source}: could not fetch release: {error}")
            continue

        source_tags[source] = current

        previous = state.get(source, "")
        if source_spec["ref"] != "latest":
            print(f"PINNED: {source}: {current} (automatic update detection disabled)")
        elif current and current != previous:
            updated_sources.append(source)
            print(f"UPDATED: {source}: {previous or '(untracked)'} -> {current}")
        elif current:
            print(f"UNCHANGED: {source}: {current}")
        else:
            print(f"WARNING: {source}: no published release found")

    output_file = os.environ.get("GITHUB_OUTPUT")
    if output_file:
        with open(output_file, "a", encoding="utf-8") as output:
            output.write(f"any_updated={'true' if updated_sources else 'false'}\n")
            output.write(f"updated_sources={','.join(sorted(updated_sources))}\n")
            output.write(f"source_tags={json.dumps(source_tags, separators=(',', ':'))}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
