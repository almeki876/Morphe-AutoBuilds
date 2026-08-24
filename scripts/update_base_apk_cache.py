"""Upload verified base APK candidates and provenance to a hidden draft Release."""

from __future__ import annotations

import logging
import mimetypes
import os
import time
from collections import defaultdict
from pathlib import Path

from github import Auth, Github
from github.GithubException import GithubException

from src.apk_cache import (
    CACHE_TAG,
    find_release,
    parse_asset_name,
    validate_asset,
)


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
UPLOAD_DIR = Path(os.getenv("BASE_APK_CACHE_UPLOAD_DIR", "base-apk-cache-in"))
KEEP_PER_PACKAGE = max(1, int(os.getenv("BASE_APK_CACHE_KEEP", "10")))


def upload_with_retry(release, path: Path) -> None:
    content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    for attempt in range(1, 4):
        try:
            release.upload_asset(
                str(path),
                label=path.name,
                content_type=content_type,
                name=path.name,
            )
            return
        except GithubException:
            if attempt == 3:
                raise
            time.sleep(5 * attempt)


def _origin_sidecar(path: Path) -> Path:
    return path.with_name(path.name + ".origin.json")


def _upload_candidates() -> list[Path]:
    """Return validated APK cache assets plus their optional origin sidecars."""
    candidates: list[Path] = []
    for path in UPLOAD_DIR.rglob("*") if UPLOAD_DIR.exists() else ():
        if not path.is_file() or path.name.endswith(".origin.json"):
            continue
        if not validate_asset(path):
            continue
        candidates.append(path)
        sidecar = _origin_sidecar(path)
        if sidecar.is_file():
            candidates.append(sidecar)
    return candidates


def main() -> None:
    token = os.environ["GITHUB_TOKEN"]
    repository = os.environ["GITHUB_REPOSITORY"]
    repo = Github(auth=Auth.Token(token)).get_repo(repository)
    release = find_release(repo)
    if release is None:
        release = repo.create_git_release(
            tag=CACHE_TAG,
            name="Automated base APK cache",
            message=(
                "Workflow-managed original APK cache. This draft release is "
                "intentionally hidden and must not be published manually."
            ),
            draft=True,
            prerelease=False,
        )
        logging.info("Created draft APK cache release %s", CACHE_TAG)

    existing = {asset.name: asset for asset in release.get_assets()}
    for path in _upload_candidates():
        if path.name in existing:
            logging.info("Already cached: %s", path.name)
            continue
        upload_with_retry(release, path)
        logging.info("Uploaded APK cache asset: %s", path.name)

    # Bound storage growth without manual cleanup. Keep the most recently
    # uploaded versions for each package; origin sidecars follow their APK.
    assets = list(release.get_assets())
    asset_by_name = {asset.name: asset for asset in assets}
    by_package: dict[str, list] = defaultdict(list)
    for asset in assets:
        if asset.name.endswith(".origin.json"):
            continue
        parsed = parse_asset_name(asset.name)
        if parsed:
            by_package[parsed[0]].append((asset, parsed[1]))

    for package, entries in by_package.items():
        versions: dict[str, list] = defaultdict(list)
        for asset, version in entries:
            versions[version].append(asset)
        ordered = sorted(
            versions.items(),
            key=lambda item: max(
                asset.created_at or asset.updated_at for asset in item[1]
            ),
            reverse=True,
        )
        for version, old_assets in ordered[KEEP_PER_PACKAGE:]:
            for asset in old_assets:
                sidecar = asset_by_name.get(asset.name + ".origin.json")
                if sidecar is not None:
                    sidecar.delete_asset()
                    logging.info("Pruned old cache origin: %s %s", package, version)
                asset.delete_asset()
                logging.info("Pruned old cache: %s %s", package, version)


if __name__ == "__main__":
    main()
