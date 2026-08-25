"""Download matrix APK inputs before the CPU-heavy patch jobs."""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import apk_cache, apk_identity, apk_validation, aurora_play, browser_fallback, downloader, google_play_metadata, providers, utils, versioning
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
    candidate = next(
        (candidate for candidate in candidates if candidate.canonical == version),
        None,
    )
    if candidate is not None:
        if candidate.code:
            return candidate
        package = providers.configured_package(app_name)
        discovered_code = (
            versioning.discovered_version_code(package, version) if package else None
        )
        if discovered_code:
            return VersionCandidate(name=candidate.name, code=discovered_code, raw=candidate.raw)
        return candidate

    config = providers.load_config(app_name, platform) or {}
    pinned = pinned_candidate(config)
    if pinned and pinned.canonical == version:
        return pinned

    package = providers.configured_package(app_name)
    discovered_code = versioning.discovered_version_code(package, version) if package else None
    return VersionCandidate(name=version, code=discovered_code)


def _preferred_play_candidate(
    app_name: str,
    package: str,
    candidates: list[VersionCandidate],
) -> VersionCandidate | None:
    del app_name, package
    return candidates[0] if candidates else None


def _new_cache_entries(before: set[Path]) -> set[Path]:
    if not apk_cache.CACHE_DIR.is_dir():
        return set()
    return {path for path in apk_cache.CACHE_DIR.iterdir() if path.is_file() and path not in before}


def _cache_snapshot() -> set[Path]:
    if not apk_cache.CACHE_DIR.is_dir():
        return set()
    return {path for path in apk_cache.CACHE_DIR.iterdir() if path.is_file()}


def _egress_policy(app_name: str) -> str:
    """Return the provider-neutral network routing policy for an app."""
    metadata = providers.load_app_metadata(app_name)
    value = str(metadata.get("egress_policy", "auto")).strip().lower()
    if value not in {"auto", "japan-first"}:
        logging.warning(
            "Unknown egress_policy=%r for %s; falling back to auto",
            value,
            app_name,
        )
        return "auto"
    return value


def _tailscale_fallback_active() -> bool:
    executable = shutil.which("tailscale")
    if not executable:
        return False
    try:
        result = subprocess.run(
            [executable, "status", "--json"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def _japan_handoff_enabled(app_name: str) -> bool:
    """Only the Actions matrix job has the workflow stage that can connect Tailscale."""
    return (
        os.getenv("GITHUB_ACTIONS", "").lower() == "true"
        and os.getenv("APP_NAME", "") == app_name
    )


def _request_japan_first_handoff(app_name: str) -> None:
    """Skip known-useless non-JP network work for apps that require Japanese egress."""
    if _egress_policy(app_name) != "japan-first":
        return
    if not _japan_handoff_enabled(app_name) or _tailscale_fallback_active():
        return
    raise RuntimeError(
        f"{app_name} is configured for japan-first egress; requesting the workflow "
        "Tailscale retry before Google Play/provider network access"
    )


def _final_tailscale_provider_retry_enabled(app_name: str) -> bool:
    """Use the existing tailnet only in the final provider-rescue child process."""
    return (
        bool(os.getenv("GITHUB_RUN_ID", "").strip())
        and not os.getenv("GITHUB_ACTIONS", "").strip()
        and os.getenv("APP_NAME", "") == app_name
        and os.getenv("MORPHE_TAILSCALE_PROVIDER_RETRY", "") != "1"
        and _tailscale_fallback_active()
    )


def _enable_unique_japan_exit_node() -> None:
    """Re-enable the one advertised Tailscale exit node and verify JP egress."""
    from scripts.resolve_tailscale_exit_node import resolve_exit_node_ip

    executable = shutil.which("tailscale")
    if not executable:
        raise RuntimeError("tailscale CLI is unavailable for final provider retry")
    status_result = subprocess.run(
        [executable, "status", "--json"],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    status = json.loads(status_result.stdout)
    exit_node_ip = resolve_exit_node_ip(status, "")
    subprocess.run(
        ["sudo", executable, "set", f"--exit-node={exit_node_ip}"],
        check=True,
        timeout=15,
    )
    subprocess.run(
        [executable, "ping", exit_node_ip],
        check=True,
        timeout=15,
    )
    verify = subprocess.run(
        [sys.executable, "scripts/verify_japan_egress.py"],
        check=False,
        timeout=60,
    )
    if verify.returncode != 0:
        raise RuntimeError("final Tailscale provider retry did not verify Japanese egress")


def _validate_downloaded_identity(
    app_name: str,
    input_apk: Path,
    package: str,
    candidate: VersionCandidate | None,
    *,
    require_japanese: bool = True,
) -> apk_identity.ApkIdentity:
    identity = apk_identity.validate_identity(
        input_apk,
        package,
        candidate,
        require_japanese=require_japanese,
    )
    apk_validation.validate_required_entries(
        input_apk, providers.required_apk_entries(app_name)
    )
    logging.info(
        "🪪 Verified APK identity: package=%s versionName=%s versionCode=%s",
        identity.package_name,
        identity.version_name,
        identity.version_code or "unknown",
    )
    return identity


def _record_cached_download(
    app_name: str,
    package: str,
    arch: str,
    input_apk: Path,
    version: str,
) -> None:
    from src import provenance

    provenance.record(
        app_name,
        version,
        "cache",
        input_apk,
        arch,
        config={"package": package},
    )


def _restore_cached_candidate(
    app_name: str,
    package: str,
    arch: str,
    candidates: list[VersionCandidate],
) -> tuple[Path, str] | None:
    """Restore an exact compatible APK before any provider network request."""
    for candidate in candidates:
        version = candidate.canonical
        if not version:
            continue
        cached = apk_cache.restore(
            package,
            version,
            app_name,
            require_japanese=providers.google_play_only(app_name),
        )
        if cached is None:
            continue
        try:
            _validate_downloaded_identity(app_name, cached, package, candidate)
        except (apk_identity.ApkIdentityError, apk_validation.ApkValidationError) as error:
            logging.warning(
                "APK cache returned an identity mismatch for %s %s: %s",
                app_name,
                version,
                error,
            )
            cached.unlink(missing_ok=True)
            continue
        _record_cached_download(app_name, package, arch, cached, version)
        logging.info(
            "⚡ Exact Base APK cache hit for %s v%s; skipping Google Play, "
            "Tailscale, and mirror providers",
            app_name,
            version,
        )
        return cached, version
    return None


def _restore_current_play_cache(
    app_name: str,
    package: str,
    arch: str,
    candidates: list[VersionCandidate],
) -> tuple[Path, str] | None:
    """Reuse the cached current Play APK for unrestricted patch policies.

    A metadata details request is tiny compared with downloading an APK. Exact
    patch versions continue to use the existing candidate cache path and never
    get overridden by the current Play release.
    """
    if any(candidate.canonical for candidate in candidates):
        return None
    current = google_play_metadata.current_release_identity(package)
    if current is None:
        logging.info(
            "Google Play current metadata unavailable for %s; continuing with normal routing",
            app_name,
        )
        return None
    logging.info(
        "🔎 Google Play current release for %s is %s; checking durable APK cache before download",
        app_name,
        current.describe(),
    )
    return _restore_cached_candidate(app_name, package, arch, [current])


def _record_play_download(
    app_name: str,
    package: str,
    arch: str,
    input_apk: Path,
    version: str,
    *,
    require_japanese: bool,
) -> None:
    apk_cache.stage(
        input_apk,
        package,
        version,
        "aurora-google-play",
        require_japanese=require_japanese,
    )
    from src import provenance
    provenance.record(
        app_name,
        version,
        "aurora-google-play",
        input_apk,
        arch,
        config={"package": package},
    )


def _download(
    app_name: str,
    source: str,
    arch: str,
    *,
    skip_play: bool = False,
) -> tuple[Path, str]:
    package = providers.configured_package(app_name)
    if not package:
        raise RuntimeError(f"No package ID configured for {app_name}")
    _, cli, bundle = _find_tools(source)
    patch_candidates = utils.get_supported_version_candidates(package, str(cli), str(bundle))
    candidates = providers.resolve_patch_candidates(app_name, package, patch_candidates)
    if patch_candidates:
        logging.info(
            "🧩 Patch-compatible release identities for %s: %s",
            app_name,
            ", ".join(candidate.describe() for candidate in candidates),
        )

    # Cache is authoritative only after package/version identity validation and
    # is therefore safe to check before any external APK origin. This is the
    # fastest path for repeated builds and avoids needless Play/Tailscale work.
    if not skip_play:
        restored = _restore_cached_candidate(app_name, package, arch, candidates)
        if restored is not None:
            return restored

    identity_errors: list[str] = []

    def try_providers() -> tuple[Path, str] | None:
        for platform in providers.download_priority(app_name):
            cache_before = _cache_snapshot()
            input_apk, version = downloader.download_platform(
                app_name,
                platform,
                str(cli),
                str(bundle),
                arch,
                version_candidates=candidates,
                require_japanese=play_only,
            )
            if not input_apk:
                continue
            version = str(version)
            candidate = _expected_candidate(app_name, platform, version, candidates)
            try:
                _validate_downloaded_identity(
                    app_name,
                    input_apk,
                    package,
                    candidate,
                    require_japanese=play_only,
                )
            except (apk_identity.ApkIdentityError, apk_validation.ApkValidationError) as error:
                identity_errors.append(f"{platform}: {error}")
                logging.error("❌ %s: rejecting mislabeled APK for %s: %s", platform, app_name, error)
                input_apk.unlink(missing_ok=True)
                downloader.remove_apk_origin(app_name, arch)
                for staged in _new_cache_entries(cache_before):
                    staged.unlink(missing_ok=True)
                continue
            return input_apk, version
        return None

    play_only = providers.google_play_only(app_name)
    play_first = providers.google_play_first(app_name)
    play_enabled = aurora_play.google_play_enabled(package)
    if play_only and not play_enabled:
        raise RuntimeError(
            f"Source policy for {app_name} requires Google Play, but Google Play is disabled"
        )
    if play_only and skip_play:
        raise RuntimeError(
            f"Source policy for {app_name} requires Google Play; refusing provider-only retry"
        )

    if play_only and play_enabled and not skip_play:
        restored_current = _restore_current_play_cache(
            app_name, package, arch, candidates
        )
        if restored_current is not None:
            return restored_current

    # Provider-chain apps prefer public providers; Play-first/only apps skip
    # this block and enter the common Google Play path below.
    if not play_first and not skip_play:
        provider_result = try_providers()
        if provider_result is not None:
            return provider_result

        if play_enabled:
            restored_current = _restore_current_play_cache(
                app_name, package, arch, candidates
            )
            if restored_current is not None:
                return restored_current

    # Known region-bound apps should not spend time on a network route that is
    # known to be unusable. The workflow owns Tailscale setup; this process only
    # requests the handoff. Once Tailscale is active the same common code runs.
    if play_enabled and not skip_play:
        _request_japan_first_handoff(app_name)

    if play_enabled and not skip_play:
        play_candidate = _preferred_play_candidate(app_name, package, candidates)
        play_input: Path | None = None
        try:
            play_input = aurora_play.download_candidate(
                package,
                play_candidate,
                Path("."),
                require_japanese=play_only,
            )
            if not apk_cache.is_valid_apk_archive(
                play_input, require_japanese=play_only
            ):
                play_input.unlink(missing_ok=True)
                raise RuntimeError("Google Play returned a corrupt APK archive")
            identity = _validate_downloaded_identity(
                app_name,
                play_input,
                package,
                play_candidate,
                require_japanese=play_only,
            )
            version = identity.version_name or identity.version_code or "unknown"
            _record_play_download(
                app_name,
                package,
                arch,
                play_input,
                version,
                require_japanese=play_only,
            )
            logging.info("✅ Google Play selected as APK origin for %s v%s", app_name, version)
            return play_input, version
        except (apk_identity.ApkIdentityError, apk_validation.ApkValidationError) as error:
            if play_input is not None:
                play_input.unlink(missing_ok=True)
            identity_errors.append(f"aurora-google-play: {error}")
            if play_only:
                raise RuntimeError(
                    f"Google Play-only source failed identity validation for {app_name}: {error}"
                ) from error
            logging.warning(
                "⚠️  Google Play release does not match the requested release for %s: %s; "
                "continuing with fallback downloaders",
                app_name,
                error,
            )

        except Exception as error:
            if play_input is not None:
                play_input.unlink(missing_ok=True)
            if play_only:
                raise RuntimeError(
                    f"Google Play-only source failed for {app_name}: {type(error).__name__}: {error}"
                ) from error
            logging.warning(
                "⚠️  Google Play rescue download failed for %s: %s: %s; "
                "continuing with fallback downloaders",
                app_name,
                type(error).__name__,
                error,
            )

    if play_first and not play_only and not skip_play:
        provider_result = try_providers()
        if provider_result is not None:
            return provider_result

    if skip_play:
        logging.info(
            "⏭️  Final Tailscale rescue skips Google Play and retries provider/CDN origins only for %s",
            app_name,
        )
    elif not play_enabled:
        logging.info(
            "⏭️  Google Play disabled by repository policy for %s; using configured provider only",
            app_name,
        )

    if skip_play:
        provider_result = try_providers()
        if provider_result is not None:
            return provider_result

    if not play_enabled:
        suffix = f" Identity errors: {'; '.join(identity_errors)}" if identity_errors else ""
        raise RuntimeError(
            f"Configured GitHub-only provider failed for {app_name}; refusing mirror fallback.{suffix}"
        )

    version = next((candidate.canonical for candidate in candidates), None)
    if not version:
        suffix = f" Identity errors: {'; '.join(identity_errors)}" if identity_errors else ""
        raise RuntimeError(f"Could not resolve a fallback version for {app_name}.{suffix}")

    fallback_candidate = next(
        (candidate for candidate in candidates if candidate.canonical == version),
        VersionCandidate(name=version),
    )
    discovered_code = versioning.discovered_version_code(package, version)
    if not fallback_candidate.code and discovered_code:
        fallback_candidate = VersionCandidate(
            name=fallback_candidate.name,
            code=discovered_code,
            raw=fallback_candidate.raw,
        )
    fallback_errors: list[str] = []
    for fallback_name, fallback_downloader in (
        ("justapk", downloader.download_with_justapk),
        ("apkeep", downloader.download_with_apkeep),
    ):
        input_apk: Path | None = None
        try:
            input_apk = fallback_downloader(package, version, Path("."))
            if not apk_cache.is_valid_apk_archive(
                input_apk, require_japanese=play_only
            ):
                input_apk.unlink(missing_ok=True)
                raise RuntimeError("returned HTML or a corrupt APK archive")
            _validate_downloaded_identity(
                app_name,
                input_apk,
                package,
                fallback_candidate,
                require_japanese=play_only,
            )
        except (apk_identity.ApkIdentityError, apk_validation.ApkValidationError) as error:
            if input_apk is not None:
                input_apk.unlink(missing_ok=True)
            identity_errors.append(f"{fallback_name}: {error}")
            fallback_errors.append(f"{fallback_name}: {error}")
            logging.warning("⚠️  %s fallback returned a mismatched APK for %s: %s", fallback_name, app_name, error)
            continue
        except Exception as error:
            if input_apk is not None:
                input_apk.unlink(missing_ok=True)
            fallback_errors.append(f"{fallback_name}: {type(error).__name__}: {error}")
            logging.warning("⚠️  %s fallback failed for %s: %s", fallback_name, app_name, error)
            continue

        apk_cache.stage(
            input_apk,
            package,
            version,
            fallback_name,
            require_japanese=play_only,
        )
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

    browser_name = "browser-uptodown"
    browser_input: Path | None = None
    try:
        browser_input = browser_fallback.download_candidate(
            app_name,
            package,
            fallback_candidate,
            Path("."),
        )
        if not apk_cache.is_valid_apk_archive(
            browser_input, require_japanese=play_only
        ):
            browser_input.unlink(missing_ok=True)
            raise RuntimeError("returned HTML or a corrupt APK archive")
        _validate_downloaded_identity(
            app_name,
            browser_input,
            package,
            fallback_candidate,
            require_japanese=play_only,
        )
        apk_cache.stage(
            browser_input,
            package,
            version,
            browser_name,
            require_japanese=play_only,
        )
        from src import provenance
        provenance.record(
            app_name,
            version,
            browser_name,
            browser_input,
            arch,
            config={"package": package},
        )
        logging.info("✅ %s selected as final APK origin for %s v%s", browser_name, app_name, version)
        return browser_input, version
    except (apk_identity.ApkIdentityError, apk_validation.ApkValidationError) as error:
        if browser_input is not None:
            browser_input.unlink(missing_ok=True)
        identity_errors.append(f"{browser_name}: {error}")
        fallback_errors.append(f"{browser_name}: {error}")
        logging.warning("⚠️  %s returned a mismatched APK for %s: %s", browser_name, app_name, error)
    except Exception as error:
        if browser_input is not None:
            browser_input.unlink(missing_ok=True)
        fallback_errors.append(
            f"{browser_name}: {type(error).__name__}: {utils.safe_text_for_log(error)}"
        )
        logging.warning(
            "⚠️  %s fallback failed for %s: %s",
            browser_name,
            app_name,
            utils.safe_text_for_log(error),
        )

    detail = "; ".join(fallback_errors)
    if _final_tailscale_provider_retry_enabled(app_name):
        logging.warning(
            "🛟 All normal provider/CDN paths failed for %s; trying them once more through Tailscale JP",
            app_name,
        )
        try:
            _enable_unique_japan_exit_node()
            os.environ["MORPHE_TAILSCALE_PROVIDER_RETRY"] = "1"
            return _download(app_name, source, arch, skip_play=True)
        except Exception as error:
            logging.warning(
                "⚠️  Final Tailscale provider retry failed for %s: %s",
                app_name,
                utils.safe_text_for_log(error),
            )
            detail = f"{detail}; tailscale-provider-retry: {type(error).__name__}: {utils.safe_text_for_log(error)}"
        finally:
            os.environ.pop("MORPHE_TAILSCALE_PROVIDER_RETRY", None)

    raise RuntimeError(f"all APK fallbacks failed for {app_name}: {detail}")


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
