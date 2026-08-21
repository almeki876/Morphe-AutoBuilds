"""Download build tools declared by ``sources/*.json``.

The source files are the single source of truth for Morphe CLI and patch bundle
repositories.  Historical one-off downloads (for example the removed private
``yuzu`` source) intentionally do not live here: if a source is not declared in
``sources/``, the workflow must not require credentials or assets for it.
"""

from __future__ import annotations

import json
import logging
import os
import pathlib
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

PATCHES_LIST_URLS: dict[str, str | None] = {
    "revanced-anddea": (
        "https://raw.githubusercontent.com/anddea/revanced-patches/refs/heads/dev/patches-list.json"
    ),
    "morphe": None,
}

SOURCES_DIR = pathlib.Path("sources")
TOOLS_DIR = pathlib.Path("tools")


def download_asset(url: str, dest: pathlib.Path, retries: int = 3) -> bool:
    """Download an asset atomically so failed attempts never leave valid-looking files."""

    for attempt in range(1, retries + 1):
        part_file: pathlib.Path | None = None
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            fd, part_name = tempfile.mkstemp(
                prefix=f".{dest.name}.", suffix=".part", dir=dest.parent
            )
            os.close(fd)
            part_file = pathlib.Path(part_name)
            result = subprocess.run(
                [
                    "curl",
                    "-fsSL",
                    "--retry",
                    "3",
                    "--retry-delay",
                    "5",
                    url,
                    "-o",
                    str(part_file),
                ],
                capture_output=True,
                text=True,
            )
            if result.returncode == 0 and part_file.stat().st_size > 0:
                part_file.replace(dest)
                logging.info("  ✅ %s (%s bytes)", dest.name, f"{dest.stat().st_size:,}")
                return True

            detail = result.stderr.strip()
            logging.warning(
                "  ⚠️  attempt %d: curl exit=%d%s",
                attempt,
                result.returncode,
                f" stderr={detail}" if detail else "",
            )
        except Exception as error:  # noqa: BLE001 - retry transport failures uniformly
            logging.warning("  ⚠️  attempt %d: %s", attempt, error)
        finally:
            if part_file is not None:
                part_file.unlink(missing_ok=True)

        if attempt < retries:
            time.sleep(10 * attempt)

    logging.error("  ❌ failed after %d attempts: %s", retries, url)
    return False


def _source_tag(source_path: pathlib.Path, source_name: str) -> str:
    key = f"SOURCE_TAG_{source_path.stem.upper().replace('-', '_')}"
    value = os.environ.get(key, "").strip()
    logging.info("  SOURCE_TAG env (%s): %s", key, value or "(not set, using sources json)")
    return value


def _wanted_asset(name: str, *, cli: bool) -> bool:
    lowered = name.lower()
    if lowered.endswith((".asc", ".sig", ".sha256", ".sha512", ".md5")):
        return False
    if cli:
        return lowered.endswith(".jar") and "sources" not in lowered and "javadoc" not in lowered
    return lowered.endswith((".mpp", ".rvp")) or (
        lowered.endswith(".jar") and "patch" in lowered
    )


def main() -> int:
    from src import utils

    failures: list[str] = []
    declared_sources = sorted(SOURCES_DIR.glob("*.json"))
    if not declared_sources:
        logging.error("No source declarations were found in %s", SOURCES_DIR)
        return 1

    for source_path in declared_sources:
        try:
            repos_info = json.loads(source_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            logging.error("❌ Could not read %s: %s", source_path, error)
            failures.append(f"{source_path.name}: invalid source declaration")
            continue

        # Bundle/dictionary files are metadata rather than downloadable source lists.
        if not isinstance(repos_info, list) or len(repos_info) < 2:
            continue

        source_name = str(repos_info[0].get("name", source_path.stem))
        env_tag = _source_tag(source_path, source_name)
        dest_dir = TOOLS_DIR / source_name
        dest_dir.mkdir(parents=True, exist_ok=True)
        logging.info("\n📦 Downloading tools for source: %s", source_name)

        for repo_idx, repo_info in enumerate(repos_info[1:]):
            if not isinstance(repo_info, dict):
                continue
            user = str(repo_info.get("user", "")).strip()
            repo = str(repo_info.get("repo", "")).strip()
            declared_tag = str(repo_info.get("tag", "latest")).strip() or "latest"
            if not user or not repo:
                failures.append(f"{source_name}: invalid repository declaration")
                continue

            is_cli_repo = repo_idx == 0
            tag = declared_tag if is_cli_repo else (env_tag or declared_tag)
            try:
                if repo_info.get("gitlab"):
                    release = utils.detect_gitlab_release(user, repo, tag)
                else:
                    release = utils.detect_github_release(user, repo, tag)
            except Exception as error:  # noqa: BLE001 - upstream APIs vary
                logging.error("  ❌ Could not fetch release for %s/%s: %s", user, repo, error)
                failures.append(f"{source_name}: {user}/{repo}")
                continue

            all_assets = release.get("assets", [])
            matched_any = False
            for asset in all_assets:
                asset_name = str(asset.get("name", ""))
                if not _wanted_asset(asset_name, cli=is_cli_repo):
                    continue

                matched_any = True
                dest_file = dest_dir / asset_name
                if dest_file.exists() and dest_file.stat().st_size > 0:
                    logging.info("  ⏭️  already exists: %s", asset_name)
                    continue

                url = str(asset.get("browser_download_url", "")).strip()
                if not url:
                    failures.append(f"{source_name}: missing URL for {asset_name}")
                    continue
                logging.info("  ⬇️  %s", asset_name)
                if not download_asset(url, dest_file):
                    failures.append(f"{source_name}: {asset_name}")

            if not matched_any:
                role = "CLI" if is_cli_repo else "patches"
                available = [str(asset.get("name", "")) for asset in all_assets] or [
                    "(no assets in release)"
                ]
                logging.error(
                    "  ❌ No %s asset matched for %s/%s@%s. Available assets: %s",
                    role,
                    user,
                    repo,
                    tag,
                    available,
                )
                failures.append(
                    f"{source_name}: no {role} asset found in {user}/{repo}@{tag}"
                )

        patches_list_url = PATCHES_LIST_URLS.get(source_name)
        if patches_list_url:
            dest_file = dest_dir / "patches-list.json"
            if dest_file.exists() and dest_file.stat().st_size > 0:
                logging.info("  ⏭️  already exists: patches-list.json")
            else:
                logging.info("  ⬇️  patches-list.json (from raw)")
                if not download_asset(patches_list_url, dest_file):
                    failures.append(f"{source_name}: patches-list.json")

    if failures:
        logging.warning("\n⚠️  %d download(s) failed:", len(failures))
        for failure in failures:
            logging.warning("  - %s", failure)
        return 1

    logging.info("\n✅ All declared source tools downloaded successfully.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
