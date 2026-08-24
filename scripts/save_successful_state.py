"""Persist upstream state only after the complete build and release succeeds.

This successful-state job runs only after the integrated GitHub Release has
been created.  Keep the direct-download catalog in the same transaction so it
cannot lag behind a successful Release because a secondary workflow failed to
trigger.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import runpy
import subprocess
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


STATE_FILE = Path("last-tags.json")
DIRECT_DOWNLOAD_FILE = Path("Morphe-AutoBuilds-Direct-Download.md")
SOURCE_ENV = {
    "morphe": "SOURCE_TAG_MORPHE",
    "anddea": "SOURCE_TAG_ANDDEA",
    "rushiranpise": "SOURCE_TAG_RUSHIRANPISE",
    "rookie": "SOURCE_TAG_ROOKIE",
    "tosox": "SOURCE_TAG_TOSOX",
    "yuzu": "SOURCE_TAG_YUZU",
    "dropped": "SOURCE_TAG_DROPPED",
}


def _github_headers() -> dict[str, str]:
    return {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {os.environ.get('GITHUB_TOKEN', '')}",
        "User-Agent": "Morphe-AutoBuilds",
    }


def latest_github_tag(owner: str, repo: str) -> str:
    request = Request(
        f"https://api.github.com/repos/{owner}/{repo}/releases?per_page=100",
        headers=_github_headers(),
    )
    with urlopen(request, timeout=30) as response:
        releases = json.load(response)
    releases = [release for release in releases if release.get("published_at")]
    releases.sort(key=lambda release: release["published_at"], reverse=True)
    return releases[0]["tag_name"] if releases else ""


def _release_history(repository: str | None = None) -> list[dict]:
    """Fetch all public Releases used by the direct-download catalog.

    Pagination matters because the catalog intentionally keeps the newest known
    APK for every configured app/source/architecture, even when that APK comes
    from an older Release than the one just published.
    """
    repository = (repository or os.environ.get("GITHUB_REPOSITORY", "")).strip()
    if not repository:
        raise RuntimeError("GITHUB_REPOSITORY is required to refresh direct downloads")

    releases: list[dict] = []
    page = 1
    while True:
        request = Request(
            f"https://api.github.com/repos/{repository}/releases?per_page=100&page={page}",
            headers=_github_headers(),
        )
        with urlopen(request, timeout=30) as response:
            batch = json.load(response)
        if not isinstance(batch, list):
            raise RuntimeError("GitHub releases API returned a non-list payload")
        releases.extend(item for item in batch if isinstance(item, dict))
        if len(batch) < 100:
            break
        page += 1
    return releases


def _refresh_direct_download_catalog(path: Path = DIRECT_DOWNLOAD_FILE) -> None:
    """Regenerate the catalog from Releases that already exist on GitHub."""
    from scripts.generate_direct_download_md import render

    releases = _release_history()
    path.write_text(render(releases), encoding="utf-8")
    newest = next((item for item in releases if not item.get("draft")), None)
    newest_tag = str((newest or {}).get("tag_name") or "unknown")
    print(f"Refreshed direct-download catalog from GitHub Releases (newest={newest_tag}).")


def _read_state(path: Path = STATE_FILE) -> dict:
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    return state if isinstance(state, dict) else {}


def _write_state(state: dict, path: Path = STATE_FILE) -> None:
    path.write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _merge_concurrent_state(baseline: dict, desired: dict, fresh: dict) -> dict:
    """Apply only this run's state changes on top of the newest remote state.

    A documentation workflow or another writer can advance ``main`` while the
    build is running. Untouched keys keep the value from the newest remote file,
    while keys changed by this successful build remain authoritative.
    """
    merged = dict(fresh)
    all_keys = set(baseline) | set(desired)
    for key in all_keys:
        before = baseline.get(key, object())
        after = desired.get(key, object())
        if before == after:
            continue
        if key in desired:
            merged[key] = desired[key]
        else:
            merged.pop(key, None)
    return merged


def _git(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        check=check,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )


def _publish_success_outputs_to_main(
    baseline: dict,
    desired: dict,
    *,
    attempts: int = 5,
) -> None:
    """Publish successful state and the current download catalog atomically.

    Each retry resets to the newest origin/main, reapplies only this run's state
    delta, and regenerates the catalog from the GitHub Releases API.  Therefore
    a concurrent documentation commit cannot make the catalog stale again.
    """
    _git("config", "user.name", "github-actions[bot]")
    _git("config", "user.email", "github-actions[bot]@users.noreply.github.com")

    last_error = ""
    for attempt in range(1, attempts + 1):
        _git("fetch", "origin", "main")
        _git("reset", "--hard", "origin/main")

        fresh = _read_state()
        merged = _merge_concurrent_state(baseline, desired, fresh)
        _write_state(merged)
        _refresh_direct_download_catalog()
        _git("add", "--", str(STATE_FILE), str(DIRECT_DOWNLOAD_FILE))

        staged = _git(
            "diff",
            "--cached",
            "--quiet",
            "--",
            str(STATE_FILE),
            str(DIRECT_DOWNLOAD_FILE),
            check=False,
        )
        if staged.returncode == 0:
            print("Successful state and direct-download catalog are already current on main.")
            return

        _git(
            "commit",
            "-m",
            "chore: record successful state and refresh direct downloads [skip ci]",
        )
        pushed = _git("push", "origin", "HEAD:main", check=False)
        if pushed.returncode == 0:
            print(f"Published successful state and direct downloads on attempt {attempt}.")
            return

        last_error = (pushed.stdout or "git push failed").strip()
        if attempt < attempts:
            delay = min(8, 2 ** (attempt - 1))
            print(
                "main advanced during successful-state publication; retrying from "
                f"fresh origin/main in {delay}s (attempt {attempt}/{attempts})."
            )
            time.sleep(delay)

    raise RuntimeError(
        f"could not publish successful outputs after {attempts} attempts: {last_error}"
    )


def main() -> None:
    baseline = _read_state()
    state = dict(baseline)

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

    _write_state(state)
    # Resolve every current APK version while preserving prior values for
    # providers that are temporarily unavailable.
    runpy.run_path("scripts/save_apk_versions.py", run_name="__main__")
    desired = _read_state()

    # This job runs only after create-release succeeds. Publishing the direct
    # catalog here guarantees that a successful Release and its links advance
    # together instead of depending on a second workflow_run trigger.
    if os.getenv("GITHUB_ACTIONS", "").lower() == "true":
        _publish_success_outputs_to_main(baseline, desired)


if __name__ == "__main__":
    main()
