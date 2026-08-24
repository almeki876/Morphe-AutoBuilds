"""Stage durable Base APK cache candidates from the single shared input artifact.

The download job uploads each original APK exactly once. Build, VirusTotal, and
cache publication download that same artifact. This helper promotes only inputs
whose matching build report completed successfully, preserving the old rule
that failed builds do not advance the durable cache. Origin sidecars are copied
with the staged cache asset so future cache hits can identify the original APK
provider and source URL.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
from pathlib import Path

from src import apk_cache, apk_identity, providers
from src.versioning import VersionCandidate


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
INPUT_ROOT = Path(os.getenv("BASE_APK_SHARED_INPUT_DIR", "base-input-artifacts"))
BUILD_RESULT_ROOT = Path(os.getenv("BASE_APK_BUILD_RESULT_DIR", "build-results"))


def _successful_builds() -> set[tuple[str, str]]:
    successful: set[tuple[str, str]] = set()
    for path in BUILD_RESULT_ROOT.rglob("*.json") if BUILD_RESULT_ROOT.exists() else ():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict) or payload.get("status") != "success":
            continue
        app = str(payload.get("app_name") or "").strip()
        source = str(payload.get("source") or payload.get("patch_source") or "").strip()
        if app and source:
            successful.add((app, source))
    return successful


def _artifact_input(manifest_path: Path, manifest: dict) -> Path | None:
    raw = str(manifest.get("path") or "").strip()
    if not raw:
        return None
    candidate = manifest_path.parent / Path(raw).name
    return candidate if candidate.is_file() else None


def _copy_origin_sidecar(manifest_path: Path, staged: Path) -> None:
    origin = manifest_path.parent / "origin.json"
    if not origin.is_file():
        return
    target = staged.with_name(staged.name + ".origin.json")
    try:
        payload = json.loads(origin.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            return
        target.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    except (OSError, json.JSONDecodeError) as error:
        logging.warning("Could not promote APK origin metadata %s: %s", origin, error)


def main() -> int:
    successful = _successful_builds()
    if not successful:
        logging.info("No successful build reports; no Base APK cache candidates staged")
        return 0

    staged_count = 0
    seen: set[tuple[str, str, str]] = set()
    manifests = sorted(INPUT_ROOT.rglob("manifest.json")) if INPUT_ROOT.exists() else []
    for manifest_path in manifests:
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            logging.warning("Ignoring invalid shared input manifest %s: %s", manifest_path, error)
            continue
        if not isinstance(manifest, dict):
            continue
        app = str(manifest.get("app_name") or "").strip()
        source = str(manifest.get("source") or "").strip()
        version = str(manifest.get("version") or "").strip()
        if (app, source) not in successful or not version:
            continue
        package = providers.configured_package(app)
        input_apk = _artifact_input(manifest_path, manifest)
        if not package or input_apk is None:
            logging.warning("Shared input incomplete for %s/%s", app, source)
            continue
        key = (package, version, str(input_apk.resolve()))
        if key in seen:
            continue
        seen.add(key)
        try:
            apk_identity.validate_identity(input_apk, package, VersionCandidate(name=version))
        except apk_identity.ApkIdentityError as error:
            logging.warning(
                "Refusing durable cache promotion for %s %s: %s", app, version, error
            )
            continue
        staged = apk_cache.stage(input_apk, package, version, "shared-base-input")
        if staged is not None:
            _copy_origin_sidecar(manifest_path, staged)
            staged_count += 1

    logging.info(
        "Staged %d verified Base APK cache candidate(s) from shared artifacts",
        staged_count,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
