"""Retry Morphe builds only for confirmed upstream resource-toolchain regressions.

Normal builds always use the latest resolved toolchain first.  When the Morphe
CLI fails with a narrowly recognized resource rebuild signature, this module
prepares the last toolchain that a complete workflow previously recorded as
successful and lets the caller retry once with it.

The known-good tags live in ``last-tags.json``.  ``save_successful_state.py``
advances them only after a complete run succeeds without using this fallback,
so this is a moving safety anchor rather than a permanent version pin.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path
from typing import Iterable


STATE_FILE = Path("last-tags.json")
SOURCE_FILE = Path("sources/morphe.json")
PRIMARY_TOOLS_DIR = Path("tools/morphe")
KNOWN_GOOD_TOOLS_DIR = Path("tools/morphe-known-good")
PRIMARY_BACKUP_DIR = Path("tools/morphe-primary-failed")
METADATA_FILE = "toolchain.json"

# Run #556 exposed this exact class of upstream regression after the latest
# Morphe CLI/patch bundle advanced.  Keep the gate deliberately narrow: patch
# fingerprint failures, unsupported patches, download failures, etc. must stay
# visible instead of being hidden by a generic old-version retry.
_RESOURCE_REGRESSION_MARKERS = (
    "xmlencodeexception",
    "unexpected array value",
)
_TOOL_VERSION_RE = re.compile(r"(?<!\d)(\d+\.\d+\.\d+(?:-dev\.\d+)?)")


def is_known_resource_regression(output: str) -> bool:
    lowered = output.casefold()
    return all(marker in lowered for marker in _RESOURCE_REGRESSION_MARKERS)


def is_morphe_build_command(command: Iterable[str]) -> bool:
    parts = [str(part) for part in command]
    return len(parts) >= 3 and parts[-2:] == ["-m", "src"]


def should_retry(source: str, command: Iterable[str], output: str) -> bool:
    if os.environ.get("MORPHE_TOOLCHAIN_FALLBACK_DISABLED", "").casefold() == "true":
        return False
    return (
        source == "morphe"
        and is_morphe_build_command(command)
        and is_known_resource_regression(output)
    )


def known_good_tags(state_path: Path = STATE_FILE) -> tuple[str, str]:
    state = json.loads(state_path.read_text(encoding="utf-8"))
    if not isinstance(state, dict):
        raise RuntimeError("last-tags.json must contain a JSON object")
    cli_tag = str(state.get("morphe_cli") or "").strip()
    patch_tag = str(state.get("morphe") or "").strip()
    if not cli_tag or cli_tag in {"latest", "unknown"}:
        raise RuntimeError("no last-known-good Morphe CLI tag is recorded")
    if not patch_tag or patch_tag in {"latest", "unknown"}:
        raise RuntimeError("no last-known-good Morphe patch tag is recorded")
    return cli_tag, patch_tag


def _tag_from_files(paths: list[Path]) -> str:
    for path in sorted(paths, key=lambda item: item.name):
        match = _TOOL_VERSION_RE.search(path.name)
        if match:
            return f"v{match.group(1)}"
    return ""


def primary_toolchain_tags(
    directory: Path = PRIMARY_TOOLS_DIR,
) -> tuple[str, str] | None:
    """Infer the exact tested primary tags from downloaded release asset names."""
    if not directory.is_dir():
        return None
    cli_tag = _tag_from_files(
        [
            path
            for path in directory.iterdir()
            if path.is_file()
            and path.suffix == ".jar"
            and "sources" not in path.name.casefold()
            and "javadoc" not in path.name.casefold()
        ]
    )
    patch_tag = _tag_from_files(
        [path for path in directory.iterdir() if path.is_file() and path.suffix == ".mpp"]
    )
    if not cli_tag or not patch_tag:
        return None
    return cli_tag, patch_tag


def _read_report(report_path: Path) -> dict | None:
    if not report_path.is_file():
        return None
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return report if isinstance(report, dict) else None


def _write_report(report_path: Path, report: dict) -> None:
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def annotate_primary_success(
    report_path: Path = Path("build-metadata/build-report.json"),
    tools_dir: Path = PRIMARY_TOOLS_DIR,
) -> None:
    """Record the exact latest-first toolchain that actually completed this build."""
    report = _read_report(report_path)
    if not report or report.get("source") != "morphe" or report.get("status") != "success":
        return
    tags = primary_toolchain_tags(tools_dir)
    if tags is None:
        return
    cli_tag, patch_tag = tags
    report.update(
        {
            "toolchain_fallback_used": False,
            "toolchain_primary_cli_tag": cli_tag,
            "toolchain_primary_patch_tag": patch_tag,
        }
    )
    _write_report(report_path, report)


def _release_assets(release: dict, *, cli: bool) -> list[dict]:
    selected: list[dict] = []
    for asset in release.get("assets", []):
        if not isinstance(asset, dict):
            continue
        name = str(asset.get("name") or "")
        url = str(asset.get("browser_download_url") or "")
        if not name or not url:
            continue
        lowered = name.casefold()
        if lowered.endswith((".asc", ".sig", ".sha256", ".sha512", ".md5")):
            continue
        if cli:
            if name.endswith(".jar") and "sources" not in lowered and "javadoc" not in lowered:
                selected.append(asset)
        elif name.endswith(".mpp"):
            selected.append(asset)
    return selected


def _valid_cached_known_good(directory: Path, cli_tag: str, patch_tag: str) -> bool:
    metadata_path = directory / METADATA_FILE
    if not metadata_path.is_file():
        return False
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return (
        metadata.get("cli_tag") == cli_tag
        and metadata.get("patch_tag") == patch_tag
        and any(path.suffix == ".jar" for path in directory.iterdir() if path.is_file())
        and any(path.suffix == ".mpp" for path in directory.iterdir() if path.is_file())
    )


def prepare_known_good_tools(
    *,
    state_path: Path = STATE_FILE,
    source_path: Path = SOURCE_FILE,
    destination: Path = KNOWN_GOOD_TOOLS_DIR,
) -> dict[str, str]:
    """Download the recorded known-good Morphe CLI and patch bundle on demand."""
    cli_tag, patch_tag = known_good_tags(state_path)
    if destination.is_dir() and _valid_cached_known_good(destination, cli_tag, patch_tag):
        return json.loads((destination / METADATA_FILE).read_text(encoding="utf-8"))

    repositories = json.loads(source_path.read_text(encoding="utf-8"))
    if not isinstance(repositories, list) or len(repositories) < 3:
        raise RuntimeError("sources/morphe.json must declare CLI and patch repositories")
    cli_repo = repositories[1]
    patch_repo = repositories[2]

    # Imported lazily so policy/unit tests can exercise signature and state
    # handling without requiring network-facing dependencies.
    from scripts.download_all_tools import download_asset
    from src import utils

    cli_release = utils.detect_github_release(
        str(cli_repo["user"]), str(cli_repo["repo"]), cli_tag
    )
    patch_release = utils.detect_github_release(
        str(patch_repo["user"]), str(patch_repo["repo"]), patch_tag
    )
    cli_assets = _release_assets(cli_release, cli=True)
    patch_assets = _release_assets(patch_release, cli=False)
    if not cli_assets:
        raise RuntimeError(f"known-good Morphe CLI release {cli_tag} has no runnable JAR asset")
    if not patch_assets:
        raise RuntimeError(f"known-good Morphe patch release {patch_tag} has no .mpp asset")

    shutil.rmtree(destination, ignore_errors=True)
    destination.mkdir(parents=True, exist_ok=True)
    for asset in cli_assets + patch_assets:
        target = destination / str(asset["name"])
        if not download_asset(str(asset["browser_download_url"]), target):
            raise RuntimeError(f"failed to download known-good Morphe asset {asset['name']}")

    metadata = {
        "cli_tag": cli_tag,
        "patch_tag": patch_tag,
        "cli_repository": f"{cli_repo['user']}/{cli_repo['repo']}",
        "patch_repository": f"{patch_repo['user']}/{patch_repo['repo']}",
    }
    (destination / METADATA_FILE).write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return metadata


def _remove_root_tool_copies(names: set[str]) -> None:
    for name in names:
        candidate = Path(name)
        if candidate.is_file():
            candidate.unlink(missing_ok=True)


def _clean_failed_build_outputs() -> None:
    for pattern in (".build-input-*", "*-patch-v*.apk"):
        for path in Path(".").glob(pattern):
            if path.is_file():
                path.unlink(missing_ok=True)
    app_name = os.environ.get("APP_NAME", "").strip()
    if app_name:
        for path in Path(".").glob(f"{app_name}-*-morphe-v*.apk"):
            if path.is_file():
                path.unlink(missing_ok=True)
    report = Path("build-metadata/build-report.json")
    report.unlink(missing_ok=True)
    shutil.rmtree("morphe-data", ignore_errors=True)


def activate_known_good_toolchain() -> dict[str, str]:
    """Replace only this job's Morphe tool directory with the known-good copy."""
    metadata = prepare_known_good_tools()
    if not PRIMARY_TOOLS_DIR.is_dir():
        raise RuntimeError(f"primary Morphe tools directory is missing: {PRIMARY_TOOLS_DIR}")

    primary_names = {
        path.name for path in PRIMARY_TOOLS_DIR.iterdir() if path.is_file()
    }
    fallback_names = {
        path.name for path in KNOWN_GOOD_TOOLS_DIR.iterdir() if path.is_file()
    }
    _remove_root_tool_copies(primary_names | fallback_names)
    _clean_failed_build_outputs()

    shutil.rmtree(PRIMARY_BACKUP_DIR, ignore_errors=True)
    shutil.move(str(PRIMARY_TOOLS_DIR), str(PRIMARY_BACKUP_DIR))
    shutil.copytree(KNOWN_GOOD_TOOLS_DIR, PRIMARY_TOOLS_DIR)
    return metadata


def annotate_build_report(
    metadata: dict[str, str],
    *,
    reason: str,
    retry_succeeded: bool,
    report_path: Path = Path("build-metadata/build-report.json"),
) -> None:
    report = _read_report(report_path)
    if not report:
        return
    report.update(
        {
            "toolchain_fallback_used": True,
            "toolchain_primary_failed": True,
            "toolchain_fallback_reason": reason,
            "toolchain_fallback_cli_tag": metadata.get("cli_tag"),
            "toolchain_fallback_patch_tag": metadata.get("patch_tag"),
            "toolchain_fallback_succeeded": retry_succeeded,
        }
    )
    _write_report(report_path, report)
