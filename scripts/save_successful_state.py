"""Persist upstream state only after the complete build and release succeeds."""

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
    build is running.  Untouched keys must keep the value from the newest
    remote file, while keys changed by this successful build remain
    authoritative.
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


def _publish_state_to_main(
    baseline: dict,
    desired: dict,
    *,
    attempts: int = 5,
) -> None:
    """Publish ``last-tags.json`` without losing concurrent main updates."""
    _git("config", "user.name", "github-actions[bot]")
    _git("config", "user.email", "github-actions[bot]@users.noreply.github.com")

    last_error = ""
    for attempt in range(1, attempts + 1):
        _git("fetch", "origin", "main")
        _git("reset", "--hard", "origin/main")

        fresh = _read_state()
        merged = _merge_concurrent_state(baseline, desired, fresh)
        _write_state(merged)
        _git("add", "--", str(STATE_FILE))

        staged = _git("diff", "--cached", "--quiet", "--", str(STATE_FILE), check=False)
        if staged.returncode == 0:
            print("Successful state is already current on main.")
            return

        _git("commit", "-m", "chore: record successful build state [skip ci]")
        pushed = _git("push", "origin", "HEAD:main", check=False)
        if pushed.returncode == 0:
            print(f"Published successful state on attempt {attempt}.")
            return

        last_error = (pushed.stdout or "git push failed").strip()
        if attempt < attempts:
            delay = min(8, 2 ** (attempt - 1))
            print(
                f"main advanced during state persistence; retrying from fresh origin/main "
                f"in {delay}s (attempt {attempt}/{attempts})."
            )
            time.sleep(delay)

    raise RuntimeError(
        f"could not publish successful state after {attempts} attempts: {last_error}"
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
    # The existing script resolves every current APK version while preserving
    # prior values for providers that are temporarily unavailable.
    runpy.run_path("scripts/save_apk_versions.py", run_name="__main__")
    desired = _read_state()

    # This script is invoked only by the successful-state GitHub Actions job.
    # Publishing here turns generation + commit into one retryable transaction;
    # the following legacy workflow commit step then sees a clean tree and exits.
    if os.getenv("GITHUB_ACTIONS", "").lower() == "true":
        _publish_state_to_main(baseline, desired)


if __name__ == "__main__":
    main()
