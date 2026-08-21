"""Download matrix APK inputs before the CPU-heavy patch jobs."""

from __future__ import annotations

import json
import logging
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import apk_cache, apk_identity, aurora_play, downloader, providers, utils
from src.versioning import VersionCandidate, pinned_candidate


def _find_tools(source: str) -> tuple[list[Path], Path, Path]:
    files, source_name = downloader.download_required(source)
    is_morphe = any("morphe" in f.name.lower() and f.suffix == ".jar" for f in files)
    is_morphe = is_morphe or any(f.suffix == ".mpp" for f in files)
    is_morphe = is_morphe or "morphe" in source.lower() or "custom" in source.lower()
    cli = (
        utils.find_file(files, contains="morphe-cli", suffix=".jar", exclude=["dev"])
        or utils.find_file(files, contains="morphe", suffix=".jar")
        if is_morphe
        else utils.find_file(files, contains="revanced-cli", suffix=".jar")
    )
    bundle = (
        utils.find_latest_patch_bundle(files, (".mpp",))
        if is_morphe
        else utils.find_latest_patch_bundle(files, (".rvp", ".mpp"))
        or utils.find_file(files, contains="patches", suffix=".jar")
    )
    if not cli or not bundle:
        raise RuntimeError(f"Could not locate CLI/bundle for {source}: {files}")
    return files, cli, bundle


def _expected_candidate(
    app_name: str,
    platform: str,
    version: str,
    candidates: list[VersionCandidate],
) -> VersionCandidate:
    """Recover the release identity that caused the provider/cache lookup."""
    config = providers.load_config(app_name, platform) or {}
    pinned = pinned_candidate(config)
    if pinned and pinned.canonical == version:
        return pinned
    return next(
        (candidate for candidate in candidates if candidate.canonical == version),
        VersionCandidate(name=version),
    )


def _preferred_play_candidate(
    app_name: str,
    candidates: list[VersionCandidate],
) -> VersionCandidate | None:
    """Choose the release Play should try without guessing a versionCode.

    Patch-bundle compatibility wins. If the bundle is unpinned/Any, retain an
    explicit app-level pin when one exists. Otherwise ``None`` means current
    Google Play release.
    """
    if candidates:
        return candidates[0]
    for platform in providers.download_priority(app_name):
        try:
            config = providers.load_config(app_name, platform) or {}
        except Exception:
            continue
        pinned = pinned_candidate(config)
        if pinned:
            return pinned
    return None


def _new_cache_entries(before: set[Path]) -> set[Path]:
    if not apk_cache.CACHE_DIR.is_dir():
        return set()
    return {
        path for path in apk_cache.CACHE_DIR.iterdir()
        if path.is_file() and path not in before
    }


def _cache_snapshot() -> set[Path]:
    if not apk_cache.CACHE_DIR.is_dir():
        return set()
    return {path for path in apk_cache.CACHE_DIR.iterdir() if path.is_file()}


def _validate_downloaded_identity(
    input_apk: Path,
    package: str,
    candidate: VersionCandidate | None,
) -> apk_identity.ApkIdentity:
    """Reject APKs whose actual manifest identity differs from selection."""
    identity = apk_identity.validate_identity(input_apk, package, candidate)
    logging.info(
        "🪪 Verified APK identity: package=%s versionName=%s versionCode=%s",
        identity.package_name,
        identity.version_name,
        identity.version_code or "unknown",
    )
    return identity


def _record_play_download(
    app_name: str,
    package: str,
    arch: str,
    input_apk: Path,
    version: str,
) -> None:
    apk_cache.stage(input_apk, package, version, "aurora-google-play")
    from src import provenance

    provenance.record(
        app_name,
        version,
        "aurora-google-play",
        input_apk,
        arch,
        config={"package": package},
    )


def _download(app_name: str, source: str, arch: str) -> tuple[Path, str]:
    package = providers.configured_package(app_name)
    if not package:
        raise RuntimeError(f"No package ID configured for {app_name}")
    _, cli, bundle = _find_tools(source)
    candidates = utils.get_supported_version_candidates(package, str(cli), str(bundle))
    identity_errors: list[str] = []

    # Google Play is the preferred origin for every app. Aurora/GPlayApi asks
    # Google Play for the base APK and all required split APKs. If the requested
    # release has a known versionCode it is supplied to PurchaseHelper directly;
    # otherwise Play's current AppDetails versionCode is used and the manifest
    # gate below verifies that it is the patch-compatible release.
    play_candidate = _preferred_play_candidate(app_name, candidates)
    play_input: Path | None = None
    try:
        play_input = aurora_play.download_candidate(package, play_candidate, Path("."))
        if not apk_cache.is_valid_apk_archive(play_input):
            play_input.unlink(missing_ok=True)
            raise RuntimeError("Google Play returned a corrupt APK archive")
        identity = _validate_downloaded_identity(play_input, package, play_candidate)
        version = identity.version_name
        _record_play_download(app_name, package, arch, play_input, version)
        logging.info("✅ Google Play selected as APK origin for %s v%s", app_name, version)
        return play_input, version
    except apk_identity.ApkIdentityError as error:
        if play_input is not None:
            play_input.unlink(missing_ok=True)
        identity_errors.append(f"aurora-google-play: {error}")
        logging.warning(
            "⚠️  Google Play release does not match the requested release for %s: %s; "
            "trying configured providers",
            app_name,
            error,
        )
    except Exception as error:
        if play_input is not None:
            play_input.unlink(missing_ok=True)
        logging.warning(
            "⚠️  Google Play first-choice download failed for %s: %s: %s; "
            "trying configured providers",
            app_name,
            type(error).__name__,
            error,
        )

    for platform in providers.download_priority(app_name):
        cache_before = _cache_snapshot()
        input_apk, version = downloader.download_platform(
            app_name,
            platform,
            str(cli),
            str(bundle),
            arch,
            version_candidates=candidates,
        )
        if not input_apk:
            continue

        version = str(version)
        candidate = _expected_candidate(app_name, platform, version, candidates)
        try:
            _validate_downloaded_identity(input_apk, package, candidate)
        except apk_identity.ApkIdentityError as error:
            identity_errors.append(f"{platform}: {error}")
            logging.error(
                "❌ %s: rejecting mislabeled APK for %s: %s",
                platform,
                app_name,
                error,
            )
            input_apk.unlink(missing_ok=True)
            downloader.remove_apk_origin(app_name, arch)
            # download_platform stages provider results before returning. If
            # identity validation rejects that result, remove only files newly
            # staged by this provider attempt so the bad label cannot poison
            # the durable cache upload.
            for staged in _new_cache_entries(cache_before):
                staged.unlink(missing_ok=True)
            continue
        return input_apk, version

    version = next((candidate.canonical for candidate in candidates), None)
    if not version:
        suffix = f" Identity errors: {'; '.join(identity_errors)}" if identity_errors else ""
        raise RuntimeError(f"Could not resolve a fallback version for {app_name}.{suffix}")

    fallback_candidate = next(
        (candidate for candidate in candidates if candidate.canonical == version),
        VersionCandidate(name=version),
    )
    fallback_errors: list[str] = []
    for fallback_name, fallback_downloader in (
        ("justapk", downloader.download_with_justapk),
        ("apkeep", downloader.download_with_apkeep),
    ):
        input_apk: Path | None = None
        try:
            input_apk = fallback_downloader(package, version, Path("."))
            if not apk_cache.is_valid_apk_archive(input_apk):
                input_apk.unlink(missing_ok=True)
                raise RuntimeError("returned HTML or a corrupt APK archive")
            _validate_downloaded_identity(input_apk, package, fallback_candidate)
        except apk_identity.ApkIdentityError as error:
            if input_apk is not None:
                input_apk.unlink(missing_ok=True)
            identity_errors.append(f"{fallback_name}: {error}")
            fallback_errors.append(f"{fallback_name}: {error}")
            logging.warning(
                "⚠️  %s fallback returned a mismatched APK for %s: %s",
                fallback_name,
                app_name,
                error,
            )
            continue
        except Exception as error:
            if input_apk is not None:
                input_apk.unlink(missing_ok=True)
            fallback_errors.append(
                f"{fallback_name}: {type(error).__name__}: {error}"
            )
            logging.warning(
                "⚠️  %s fallback failed for %s: %s",
                fallback_name,
                app_name,
                error,
            )
            continue

        apk_cache.stage(input_apk, package, version, fallback_name)
        from src import provenance

        provenance.record(
            app_name,
            version,
            fallback_name,
            input_apk,
            arch,
            config={"package": package},
        )
        return input_apk, version

    detail = "; ".join(fallback_errors)
    raise RuntimeError(f"all non-browser fallbacks failed for {app_name}: {detail}")


def _configured_arch(app_name: str, source: str) -> str:
    config = json.loads(Path("arch-config.json").read_text(encoding="utf-8"))
    for entry in config:
        if entry.get("app_name") == app_name and entry.get("source") == source:
            arches = entry.get("arches") or entry.get("arch")
            if isinstance(arches, str):
                return arches
            if isinstance(arches, list) and arches:
                return str(arches[0])
    return "universal"


def main() -> None:
    app_name = os.environ["APP_NAME"]
    source = os.environ["SOURCE"]
    arch = os.environ.get("ARCH") or _configured_arch(app_name, source)
    input_apk, version = _download(app_name, source, arch)
    output_dir = Path("base-apk-input")
    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / f"{app_name}-{source}-{arch}{input_apk.suffix.lower()}"
    shutil.copy2(input_apk, target)
    scan_dir = Path("base-apk-scan-out")
    scan_dir.mkdir(parents=True, exist_ok=True)
    scan_target = scan_dir / f"{app_name}-{source}-{arch}{input_apk.suffix.lower()}"
    shutil.copy2(input_apk, scan_target)
    metadata_path = Path("build-metadata/apk-sources.json")
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    metadata = json.loads(metadata_path.read_text(encoding="utf-8")) if metadata_path.exists() else []
    metadata = [item for item in metadata if not (
        item.get("app_name") == app_name
        and item.get("patch_source") == source
        and item.get("architecture") == arch
    )]
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    manifest = {"app_name": app_name, "source": source, "arch": arch, "version": version, "path": str(target)}
    (output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    logging.info("Downloaded %s v%s from the provider chain into %s", app_name, version, target)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    main()
