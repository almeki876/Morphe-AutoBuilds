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
import subprocess
import sys
import time
from urllib.request import Request, urlopen


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

STATE_FILE = Path("last-tags.json")
DIRECT_DOWNLOAD_FILE = Path("Morphe-AutoBuilds-Direct-Download.md")
DIRECT_DOWNLOAD_JA_FILE = Path("Morphe-AutoBuilds-Direct-Download.ja.md")
BUILD_RESULTS_DIR = Path("build-results")
def _github_headers() -> dict[str, str]:
    return {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {os.environ.get('GITHUB_TOKEN', '')}",
        "User-Agent": "Morphe-AutoBuilds",
    }


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


def _refresh_direct_download_catalog(
    path: Path = DIRECT_DOWNLOAD_FILE,
    japanese_path: Path | None = None,
) -> None:
    """Regenerate both language catalogs from Releases that already exist on GitHub."""
    from scripts.generate_direct_download_md import render

    releases = _release_history()
    if japanese_path is None:
        japanese_path = (
            DIRECT_DOWNLOAD_JA_FILE
            if path == DIRECT_DOWNLOAD_FILE
            else path.with_name(f"{path.stem}.ja{path.suffix}")
        )
    path.write_text(render(releases, language="en"), encoding="utf-8")
    japanese_path.write_text(render(releases, language="ja"), encoding="utf-8")
    newest = next((item for item in releases if not item.get("draft")), None)
    newest_tag = str((newest or {}).get("tag_name") or "unknown")
    print(f"Refreshed English and Japanese direct-download catalogs (newest={newest_tag}).")


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


# Gboard is one chained build identified by its "jason" matrix entry; a failure
# there must keep all three patch sources pending (see prepare_matrix.py).
GBOARD_SOURCES = ("jason", "adobo", "morning-entree")
_FAILED_STATUSES = {"failure", "cancelled", "skipped"}


def _read_status_fields(path: Path) -> dict[str, str]:
    fields: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return fields
    for line in text.splitlines():
        if not line.strip():
            break  # key=value header ends at the first blank line
        key, separator, value = line.partition("=")
        if separator:
            fields[key.strip()] = value.strip()
    return fields


def build_outcomes(root: Path = BUILD_RESULTS_DIR) -> tuple[set[str], set[str]]:
    """Return ``(failed_sources, failed_apps)`` from per-matrix status files.

    State is advanced per source/app instead of all-or-nothing: one broken app
    must not freeze ``last-tags.json`` for every healthy source, because a frozen
    state makes every scheduled check re-detect all sources as "updated" and
    rebuild the whole matrix again.
    """
    failed_sources: set[str] = set()
    failed_apps: set[str] = set()
    if root.is_dir():
        for path in root.rglob("*-build.txt"):
            fields = _read_status_fields(path)
            if fields.get("status", "").casefold() not in _FAILED_STATUSES:
                continue
            if fields.get("source"):
                failed_sources.add(fields["source"])
            if fields.get("app"):
                failed_apps.add(fields["app"])
    if failed_sources & set(GBOARD_SOURCES):
        failed_sources |= set(GBOARD_SOURCES)
    return failed_sources, failed_apps


def successful_builds(root: Path = BUILD_RESULTS_DIR) -> tuple[set[str], set[str]]:
    """Return sources and apps with successful matrix status artifacts."""
    successful_sources: set[str] = set()
    successful_apps: set[str] = set()
    failed_sources, failed_apps = build_outcomes(root)
    if root.is_dir():
        for path in root.rglob("*-build.txt"):
            fields = _read_status_fields(path)
            if fields.get("status", "").casefold() != "success":
                continue
            if fields.get("source"):
                successful_sources.add(fields["source"])
            if fields.get("app"):
                successful_apps.add(fields["app"])

    if "jason" in successful_sources and not failed_sources.intersection(GBOARD_SOURCES):
        successful_sources.update(GBOARD_SOURCES)
    successful_sources.difference_update(failed_sources)
    successful_apps.difference_update(failed_apps)
    return successful_sources, successful_apps


def advance_successful_source_tags(
    state: dict,
    resolved_tags: dict,
    successful_sources: set[str],
    failed_sources: set[str],
) -> None:
    """Persist only exact patch tags used by successful matrix builds."""
    for source in sorted(successful_sources - failed_sources - {"morphe"}):
        raw_value = resolved_tags.get(source)
        value = str(raw_value or "").strip()
        if value and value not in {"latest", "unknown"} and not value.startswith("{"):
            state[source] = value
        else:
            print(
                f"::warning::No exact resolved patch tag is available for successful "
                f"source {source}; preserving its previous state."
            )


def successful_apk_versions(
    root: Path,
    successful_apps: set[str],
    failed_apps: set[str],
) -> dict[str, str]:
    """Resolve unambiguous APK versions from successful build input manifests."""
    from src.versioning import canonical_version

    versions_by_app: dict[str, set[str]] = {}
    if not root.is_dir():
        return {}
    for path in sorted(root.rglob("manifest.json")):
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            print(f"::warning::Could not read APK input manifest {path}: {error}")
            continue
        if not isinstance(manifest, dict):
            print(f"::warning::Ignoring invalid APK input manifest {path}")
            continue
        app = str(manifest.get("app_name") or "")
        version = str(manifest.get("version") or "").strip()
        if app not in successful_apps or app in failed_apps:
            continue
        if not version:
            print(f"::warning::APK input manifest has no version: {path}")
            continue
        versions_by_app.setdefault(app, set()).add(canonical_version(version))

    resolved: dict[str, str] = {}
    for app, versions in versions_by_app.items():
        if len(versions) == 1:
            resolved[app] = next(iter(versions))
        else:
            print(
                f"::warning::Successful builds used conflicting APK versions for "
                f"{app}; keeping its previous recorded version: {sorted(versions)}"
            )
    return resolved


def _morphe_reports(root: Path = BUILD_RESULTS_DIR) -> list[dict]:
    reports: list[dict] = []
    if not root.is_dir():
        return reports
    for path in root.rglob("*.json"):
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(report, dict) and report.get("source") == "morphe":
            reports.append(report)
    return reports


def _morphe_fallback_used(root: Path = BUILD_RESULTS_DIR) -> bool:
    """Return whether any Morphe matrix entry needed the safety fallback."""
    return any(
        report.get("toolchain_fallback_used") is True
        for report in _morphe_reports(root)
    )


def _morphe_proven_primary_tags(
    root: Path = BUILD_RESULTS_DIR,
) -> tuple[str, str] | None:
    """Return the exact fallback-free Morphe pair proven by this workflow run.

    Never infer a known-good tag from "latest" at persistence time: upstream can
    publish another release after the matrix build but before this job. Every
    Morphe report must instead agree on the exact CLI/patch asset versions that
    actually completed successfully in this run.
    """
    reports = _morphe_reports(root)
    if not reports or any(report.get("toolchain_fallback_used") is True for report in reports):
        return None

    cli_tags: set[str] = set()
    patch_tags: set[str] = set()
    for report in reports:
        if report.get("status") != "success":
            return None
        cli_tag = str(report.get("toolchain_primary_cli_tag") or "").strip()
        patch_tag = str(report.get("toolchain_primary_patch_tag") or "").strip()
        if not cli_tag or not patch_tag:
            return None
        cli_tags.add(cli_tag)
        patch_tags.add(patch_tag)

    if len(cli_tags) != 1 or len(patch_tags) != 1:
        return None
    return next(iter(cli_tags)), next(iter(patch_tags))


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
    delta, and regenerates the catalog from the GitHub Releases API. Therefore
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
        _git(
            "add",
            "--",
            str(STATE_FILE),
            str(DIRECT_DOWNLOAD_FILE),
            str(DIRECT_DOWNLOAD_JA_FILE),
        )

        staged = _git(
            "diff",
            "--cached",
            "--quiet",
            "--",
            str(STATE_FILE),
            str(DIRECT_DOWNLOAD_FILE),
            str(DIRECT_DOWNLOAD_JA_FILE),
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
    failed_sources, failed_apps = build_outcomes()
    successful_sources, successful_apps = successful_builds()
    if failed_sources or failed_apps:
        print(
            "Partial persistence: keeping pending state for failed sources "
            f"{sorted(failed_sources)} and apps {sorted(failed_apps)}."
        )
    morphe_fallback_used = _morphe_fallback_used() or "morphe" in failed_sources
    morphe_proven_tags = (
        None if morphe_fallback_used else _morphe_proven_primary_tags()
    )
    if morphe_fallback_used:
        print(
            "Morphe last-known-good toolchain remains unchanged because at least "
            "one matrix build required fallback in this run."
        )
    elif morphe_proven_tags:
        state["morphe_cli"], state["morphe"] = morphe_proven_tags
        print(
            "Advanced Morphe last-known-good toolchain to the exact pair proven "
            f"by this run: CLI={morphe_proven_tags[0]} patches={morphe_proven_tags[1]}."
        )
    else:
        print(
            "Morphe build reports did not contain one consistent proven primary "
            "toolchain pair; retaining the existing safety anchor."
        )

    try:
        resolved_source_tags = json.loads(os.getenv("SOURCE_TAGS_JSON", "{}"))
    except json.JSONDecodeError as error:
        raise RuntimeError("SOURCE_TAGS_JSON must be valid JSON") from error
    if not isinstance(resolved_source_tags, dict):
        raise RuntimeError("SOURCE_TAGS_JSON must be a JSON object")
    advance_successful_source_tags(
        state,
        resolved_source_tags,
        successful_sources,
        failed_sources,
    )

    apk_versions = successful_apk_versions(
        Path("base-apk-inputs"),
        successful_apps,
        failed_apps,
    )
    for app, version in apk_versions.items():
        state[f"apk_{app}"] = version
    _write_state(state)
    desired = _read_state()

    # This job runs only after create-release succeeds. Publishing the direct
    # catalog here guarantees that a successful Release and its links advance
    # together instead of depending on a second workflow_run trigger.
    if os.getenv("GITHUB_ACTIONS", "").lower() == "true":
        _publish_success_outputs_to_main(baseline, desired)


if __name__ == "__main__":
    main()
