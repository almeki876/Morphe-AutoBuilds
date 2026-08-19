"""Download matrix APK inputs before the CPU-heavy patch jobs."""

from __future__ import annotations

import json
import logging
import os
import shutil
from pathlib import Path

from src import apk_cache, downloader, providers, utils


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
        utils.find_file(files, contains="patches", suffix=".mpp")
        or utils.find_file(files, suffix=".mpp")
        if is_morphe
        else utils.find_file(files, contains="patches", suffix=".rvp")
        or utils.find_file(files, contains="patches", suffix=".mpp")
        or utils.find_file(files, suffix=".mpp")
        or utils.find_file(files, contains="patches", suffix=".jar")
    )
    if not cli or not bundle:
        raise RuntimeError(f"Could not locate CLI/bundle for {source}: {files}")
    return files, cli, bundle


def _download(app_name: str, source: str, arch: str) -> tuple[Path, str]:
    package = providers.configured_package(app_name)
    if not package:
        raise RuntimeError(f"No package ID configured for {app_name}")
    _, cli, bundle = _find_tools(source)
    candidates = utils.get_supported_version_candidates(package, str(cli), str(bundle))
    for platform in providers.download_priority(app_name):
        input_apk, version = downloader.download_platform(
            app_name,
            platform,
            str(cli),
            str(bundle),
            arch,
            version_candidates=candidates,
        )
        if input_apk:
            return input_apk, str(version)

    version = next((candidate.canonical for candidate in candidates), None)
    if not version:
        raise RuntimeError(f"Could not resolve a fallback version for {app_name}")
    input_apk = downloader.download_with_fallback_chain(package, version, Path("."))
    if not apk_cache.is_valid_apk_archive(input_apk):
        input_apk.unlink(missing_ok=True)
        raise RuntimeError("fallback chain returned HTML or a corrupt APK archive")
    apk_cache.stage(input_apk, package, version, "fallback-chain")
    from src import provenance

    provenance.record(
        app_name,
        version,
        "fallback-chain",
        input_apk,
        arch,
        config={"package": package},
    )
    return input_apk, version


def main() -> None:
    app_name = os.environ["APP_NAME"]
    source = os.environ["SOURCE"]
    arch = os.environ.get("ARCH", "universal")
    input_apk, version = _download(app_name, source, arch)
    output_dir = Path("base-apk-input")
    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / f"{app_name}-{source}-{arch}{input_apk.suffix.lower()}"
    shutil.copy2(input_apk, target)
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
