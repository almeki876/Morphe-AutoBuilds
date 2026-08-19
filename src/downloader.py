import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from src import apk_cache, provenance, providers, utils
from src.downloads import normalize_download
from src.versioning import (
    VersionCandidate,
    configured_fallback_candidates,
    pinned_candidate,
)


def remove_apk_origin(app_name: str, arch: str) -> None:
    """Compatibility entry point for callers removing failed provenance."""
    provenance.remove(app_name, arch)


def download_resource(
    url: str,
    name: str = None,
    retries: int | None = None,
    referer: str = None,
    headers: dict | None = None,
) -> Path:
    if not isinstance(url, str) or not url.strip():
        raise ValueError("download URL must be a non-empty string")
    request_headers = dict(headers or {})
    if referer:
        request_headers["Referer"] = referer
    # APKMirrorのr2.cloudflarestorage URLはRefererが必須
    if not referer and "apkmirror.com" in url:
        request_headers["Referer"] = "https://www.apkmirror.com/"

    # HTTP status/transport retries happen inside cf_aware_get. The outer loop
    # protects against truncated response bodies while streaming to disk.
    stream_attempts = 2
    for stream_attempt in range(1, stream_attempts + 1):
        part_path: Path | None = None
        res = None
        try:
            res = utils.cf_aware_get(
                url,
                retries=retries,
                stream=True,
                headers=request_headers if request_headers else None,
                timeout=60,
            )
            res.raise_for_status()
            final_url = res.url
            safe_final_url = utils.safe_url_for_log(final_url)
            resolved_name = name or utils.extract_filename(
                res, fallback_url=final_url
            )
            filepath = Path(resolved_name)
            filepath.parent.mkdir(parents=True, exist_ok=True)
            total_size = int(res.headers.get("content-length", 0))
            downloaded_size = 0

            fd, part_name = tempfile.mkstemp(
                prefix=f".{filepath.name}.",
                suffix=".part",
                dir=filepath.parent,
            )
            os.close(fd)
            part_path = Path(part_name)

            with part_path.open("wb") as file:
                for chunk in res.iter_content(chunk_size=1024 * 128):
                    if chunk:
                        file.write(chunk)
                        downloaded_size += len(chunk)

            if downloaded_size <= 0:
                raise IOError(
                    f"downloaded empty response body from {safe_final_url}"
                )
            if total_size > 0 and downloaded_size != total_size:
                raise IOError(
                    f"incomplete download from {safe_final_url}: "
                    f"expected {total_size} bytes, received {downloaded_size}"
                )

            part_path.replace(filepath)
            logging.info(
                'URL: %s [%d/%d] -> "%s" [1]',
                safe_final_url,
                downloaded_size,
                total_size,
                filepath,
            )
            return filepath
        except Exception as error:
            if isinstance(error, utils.BotProtectionError):
                raise
            if stream_attempt >= stream_attempts:
                raise
            wait = utils.retry_after_seconds(None, stream_attempt)
            logging.warning(
                "APK stream failed on attempt %d/%d (%s); retrying in %.1fs",
                stream_attempt,
                stream_attempts,
                error,
                wait,
            )
            time.sleep(wait)
        finally:
            if res is not None:
                res.close()
            if part_path is not None:
                part_path.unlink(missing_ok=True)

    raise RuntimeError(f"download failed unexpectedly: {url}")


def download_required(source: str) -> tuple[list[Path], str]:
    source_path = Path("sources") / f"{source}.json"
    with source_path.open(encoding="utf-8") as json_file:
        repos_info = json.load(json_file)

    if isinstance(repos_info, dict) and "bundle_url" in repos_info:
        return download_from_bundle(repos_info)
    
    name = repos_info[0]["name"]
    downloaded_files = []

    # tools/<name>/ にキャッシュ済みファイルがあればAPIを叩かずそちらを使う
    tools_dir = Path("tools") / name
    if tools_dir.exists():
        cached = [f for f in tools_dir.iterdir()
                  if f.is_file() and not f.name.endswith(".asc") and f.stat().st_size > 0]
        if cached:
            logging.info(f"📦 Using pre-downloaded tools from {tools_dir} ({len(cached)} files)")
            for f in cached:
                dest = Path(f.name)
                if not dest.exists():
                    import shutil
                    shutil.copy2(f, dest)
                downloaded_files.append(dest)
            return downloaded_files, name
        else:
            logging.warning(f"⚠️  tools/{name}/ exists but is empty — falling back to GitHub API")

    # キャッシュなし → 従来通りGitHub APIから取得
    logging.info(f"⬇️  Downloading tools for {name} from GitHub API")
    for repo_idx, repo_info in enumerate(repos_info[1:]):
        user = repo_info['user']
        repo = repo_info['repo']
        tag = repo_info['tag']

        try:
            if repo_info.get("gitlab"):
                release = utils.detect_gitlab_release(user, repo, tag)
            else:
                release = utils.detect_github_release(user, repo, tag)
        except Exception as e:
            logging.error(f"❌ Could not fetch release for {user}/{repo}@{tag}: {e}")
            continue

        assets = release.get("assets", [])

        # sources/*.json の規約: 1番目のリポジトリエントリ=CLI/マネージャー、
        # それ以降=パッチバンドル。以前は repo名が文字列 "morphe-cli" /
        # "morphe-patches" と一致するかで判定していたが、上流のリポジトリ改名
        # （例: morphe-cli → morphe-desktop）でこの判定が崩れて何もダウンロード
        # されなくなっていた。位置ベースの判定にして改名に強くする。
        is_cli_repo = repo_idx == 0
        matched_any = False

        if is_cli_repo:
            for asset in assets:
                aname = asset["name"]
                if aname.endswith((".asc", ".sig", ".sha256", ".sha512", ".md5")):
                    continue
                # 拡張子だけで判定（CLIのファイル名は上流が自由に変更できる）。
                # .mpp はMorphe系パッチ形式、.jar はCLI本体（sources/javadocは除く）。
                if aname.endswith(".mpp") or (
                    aname.endswith(".jar")
                    and "sources" not in aname.lower()
                    and "javadoc" not in aname.lower()
                ):
                    matched_any = True
                    filepath = download_resource(asset["browser_download_url"])
                    downloaded_files.append(filepath)
        else:
            for asset in assets:
                if asset["name"].endswith(".asc"):
                    continue
                matched_any = True
                filepath = download_resource(asset["browser_download_url"])
                downloaded_files.append(filepath)

        if not matched_any:
            role = "CLI" if is_cli_repo else "patches"
            available = [a["name"] for a in assets] or ["(no assets in release)"]
            logging.error(
                f"❌ No {role} asset matched for {user}/{repo}@{tag}. "
                f"Available assets: {available}"
            )

    return downloaded_files, name

def download_from_bundle(bundle_info: dict) -> tuple[list[Path], str]:
    bundle_url = bundle_info["bundle_url"]
    name = bundle_info.get("name", "bundle-patches")
    logging.info(f"Downloading bundle from {bundle_url}")
    with utils.cf_aware_get(bundle_url) as res:
        res.raise_for_status()
        bundle_data = res.json()
    downloaded_files = []
    if "patches" in bundle_data:
        for patch in bundle_data.get("patches", []):
            if "url" in patch:
                filepath = download_resource(patch["url"])
                downloaded_files.append(filepath)
                logging.info(f"Downloaded patch: {patch.get('name', 'unknown')}")
        for integration in bundle_data.get("integrations", []):
            if "url" in integration:
                filepath = download_resource(integration["url"])
                downloaded_files.append(filepath)
                logging.info(f"Downloaded integration: {integration.get('name', 'unknown')}")
    try:
        cli_release = utils.detect_github_release("revanced", "revanced-cli", "latest")
        for asset in cli_release["assets"]:
            if asset["name"].endswith(".asc"):
                continue
            if asset["name"].endswith(".jar") and "cli" in asset["name"].lower():
                filepath = download_resource(asset["browser_download_url"])
                downloaded_files.append(filepath)
                logging.info("Downloaded ReVanced CLI")
                break
    except Exception as e:
        logging.warning(f"Could not download ReVanced CLI: {e}")
    return downloaded_files, name

def download_platform(
    app_name: str,
    platform: str,
    cli: str,
    patches: str,
    arch: str = None,
    version_candidates: list[VersionCandidate] | None = None,
) -> tuple[Path | None, str | None]:
    try:
        config = providers.load_config(app_name, platform)
        if config is None:
            logging.info(f"⏭️  {platform}: no config for {app_name}, skipping")
            return None, None

        if arch:
            config['arch'] = arch

        cache_enabled = config.get("cache", True) is not False

        # Support direct_url: skip version resolution and download directly
        direct_url = config.get("direct_url")
        if direct_url:
            logging.info(f"🔗 {platform}: using direct_url for {app_name}")
            try:
                # Try to resolve version: first check pinned version in config,
                # then try the current platform's get_latest_version,
                # then fall back through the configured provider priority.
                version = config.get("version") or None
                if not version:
                    try:
                        platform_mod = providers.MODULES.get(platform)
                        if platform_mod and hasattr(platform_mod, "get_latest_version"):
                            version = platform_mod.get_latest_version(app_name, config)
                    except Exception:
                        pass
                if not version:
                    fallback_platforms = [
                        provider
                        for provider in providers.DOWNLOAD_PRIORITY
                        if provider != platform
                    ]
                    for fb_platform in fallback_platforms:
                        try:
                            fb_config = providers.load_config(
                                app_name,
                                fb_platform,
                            )
                            if fb_config is None:
                                continue
                            fb_mod = providers.MODULES.get(fb_platform)
                            if fb_mod and hasattr(fb_mod, "get_latest_version"):
                                version = fb_mod.get_latest_version(app_name, fb_config)
                                if version:
                                    logging.info(f"🔍 {platform}: resolved version {version} for {app_name} via {fb_platform} fallback")
                                    break
                        except Exception as e:
                            logging.debug(f"direct_url version fallback via {fb_platform} failed: {e}")
                            continue
                version = version or "latest"
                package = config.get("package")
                if cache_enabled and package and version != "latest":
                    cached = apk_cache.restore(package, version, app_name)
                    if cached:
                        provenance.record(
                            app_name,
                            version,
                            "cache",
                            cached,
                            arch,
                            cached=True,
                            config=config,
                        )
                        return cached, version

                filepath = download_resource(direct_url)
                if not apk_cache.is_valid_apk_archive(filepath):
                    filepath.unlink(missing_ok=True)
                    raise ValueError("direct_url returned a non-APK response")
                logging.info(f"✅ {platform}: downloaded {app_name} via direct_url -> {filepath.name} (version={version})")
                if cache_enabled and package and version != "latest":
                    apk_cache.stage(filepath, package, version, platform)
                provenance.record(
                    app_name,
                    version,
                    platform,
                    filepath,
                    arch,
                    config=config,
                )
                return filepath, version
            except Exception as e:
                logging.error(f"❌ {platform}: direct_url download failed for {app_name}: {type(e).__name__}: {e}")
                return None, None

        pinned = pinned_candidate(config)
        candidates: list[VersionCandidate] = [pinned] if pinned else []
        configured_fallbacks = configured_fallback_candidates(config)
        if not candidates:
            if platform == "github":
                # GitHub releases carry the version in the tag — skip CLI invocation
                try:
                    latest = providers.MODULES["github"].get_latest_version(
                        app_name, config
                    )
                    candidates = (
                        [VersionCandidate(name=str(latest))] if latest else []
                    )
                except Exception as e:
                    logging.error(f"❌ github: get_latest_version failed for {app_name}: {e}")
                    return None, None
            else:
                candidates = (
                    list(version_candidates)
                    if version_candidates is not None
                    else utils.get_supported_version_candidates(
                        config["package"], cli, patches
                    )
                )
        candidates.extend(configured_fallbacks)
        platform_module = providers.MODULES[platform]

        if not candidates:
            logging.warning(f"⚠️  {platform}: CLI/patch version lookup failed for {app_name}, falling back to get_latest_version")
            try:
                latest = platform_module.get_latest_version(app_name, config)
                candidates = (
                    [VersionCandidate(name=str(latest))] if latest else []
                )
            except Exception as e:
                logging.error(f"❌ {platform}: get_latest_version failed for {app_name}: {type(e).__name__}: {e}")
                return None, None

        if not candidates:
            logging.error(f"❌ {platform}: could not resolve any version for {app_name}")
            return None, None

        candidate_limit = max(1, int(os.getenv("APK_VERSION_CANDIDATES", "5")))
        attempted: set[tuple[str, str | None]] = set()
        for candidate in candidates[:candidate_limit]:
            candidate_key = (candidate.name, candidate.code)
            if candidate_key in attempted:
                continue
            attempted.add(candidate_key)
            version = candidate.canonical
            logging.info(
                "🔍 %s: trying compatible version %s for %s",
                platform,
                candidate.describe(),
                app_name,
            )

            if cache_enabled:
                cached = apk_cache.restore(
                    config.get("package", ""), version, app_name
                )
                if cached:
                    provenance.record(
                        app_name,
                        version,
                        "cache",
                        cached,
                        arch,
                        cached=True,
                        config=config,
                    )
                    return cached, version

            try:
                candidate_resolver = getattr(
                    platform_module, "get_download_link_for_candidate", None
                )
                if candidate_resolver:
                    download_link = candidate_resolver(candidate, app_name, config)
                else:
                    download_link = None
                    alias_errors: list[str] = []
                    for alias in candidate.aliases(platform):
                        try:
                            download_link = platform_module.get_download_link(
                                alias, app_name, config
                            )
                            if download_link:
                                if alias != version:
                                    logging.info(
                                        "🔧 %s: matched %s using alias %s",
                                        platform,
                                        candidate.describe(),
                                        alias,
                                    )
                                break
                        except Exception as alias_error:
                            alias_errors.append(
                                f"{alias}: {type(alias_error).__name__}: "
                                f"{alias_error}"
                            )
                    if not download_link and alias_errors:
                        raise ValueError("; ".join(alias_errors))
                if not download_link:
                    raise ValueError("provider returned no download link")

                download_spec = normalize_download(download_link)
                referer = providers.referer(platform, app_name, config)
                filepath = download_resource(
                    download_spec.url,
                    referer=(
                        None if "Referer" in download_spec.headers else referer
                    ),
                    headers=download_spec.headers,
                )
                if not apk_cache.is_valid_apk_archive(filepath):
                    filepath.unlink(missing_ok=True)
                    raise ValueError(
                        "provider returned HTML or a corrupt APK archive"
                    )
                if cache_enabled:
                    apk_cache.stage(
                        filepath,
                        config.get("package", ""),
                        version,
                        platform,
                    )
                provenance.record(
                    app_name,
                    version,
                    platform,
                    filepath,
                    arch,
                    config=config,
                )
                logging.info(
                    "✅ %s: downloaded %s v%s -> %s",
                    platform,
                    app_name,
                    version,
                    filepath.name,
                )
                return filepath, version
            except Exception as error:
                logging.warning(
                    "⚠️  %s: compatible version %s failed for %s: %s: %s",
                    platform,
                    version,
                    app_name,
                    type(error).__name__,
                    error,
                )
                if isinstance(error, utils.BotProtectionError):
                    logging.warning(
                        "🛑 %s: bot protection detected; skipping remaining "
                        "version candidates for this provider",
                        platform,
                    )
                    break

        logging.error(
            "❌ %s: exhausted %d compatible version candidate(s) for %s",
            platform,
            len(attempted),
            app_name,
        )
        return None, None

    except Exception as e:
        logging.error(f"❌ {platform}: unexpected error for {app_name}: {type(e).__name__}: {e}")
        return None, None

def download_apkeditor() -> Path:
    release = utils.detect_github_release("REAndroid", "APKEditor", "latest")
    for asset in release["assets"]:
        if asset["name"].startswith("APKEditor") and asset["name"].endswith(".jar"):
            return download_resource(asset["browser_download_url"])
    raise RuntimeError("APKEditor .jar file not found in the latest release")


def download_with_justapk(
    package: str,
    version: str,
    output_dir: Path | None = None,
) -> Path:
    """Download an APK through justapk's mobile-source fallback."""
    from justapk import APKDownloader

    result = APKDownloader().download(
        package,
        version=version,
        output_dir=output_dir or Path("."),
    )
    filepath = Path(result.path)
    if not filepath.is_file() or filepath.stat().st_size <= 0:
        raise IOError(f"justapk returned an invalid APK path: {filepath}")
    return filepath


def download_with_apkeep(
    package: str,
    version: str,
    output_dir: Path | None = None,
) -> Path:
    """Download an APK with apkeep when its executable is available."""
    output_dir = output_dir or Path(".")
    executable = shutil.which("apkeep")
    candidates = (
        (Path(".venv") / "Scripts" / "apkeep.exe",)
        if sys.platform == "win32"
        else (Path(".venv") / "bin" / "apkeep",)
    )
    for candidate in candidates:
        if executable is None and candidate.is_file():
            executable = str(candidate)
            break
    if executable is None:
        raise FileNotFoundError("apkeep executable was not found")

    output_dir.mkdir(parents=True, exist_ok=True)
    before = {
        path: path.stat().st_mtime_ns
        for path in output_dir.rglob("*")
        if path.is_file()
    }
    subprocess.run(
        [executable, "-a", f"{package}@{version}", str(output_dir)],
        check=True,
    )
    candidates = [
        path
        for path in output_dir.rglob("*")
        if path.is_file()
        and path.suffix.lower() in {".apk", ".apkm", ".apks", ".xapk"}
        and (path not in before or path.stat().st_mtime_ns > before[path])
    ]
    if not candidates:
        raise IOError(f"apkeep produced no APK for {package}@{version}")
    return max(candidates, key=lambda path: path.stat().st_mtime_ns)


def download_with_fallback_chain(
    package: str,
    version: str,
    output_dir: Path,
) -> Path:
    """Try non-browser downloaders in priority order for any application."""
    errors: list[str] = []
    for provider, downloader in (
        ("justapk", download_with_justapk),
        ("apkeep", download_with_apkeep),
    ):
        try:
            return downloader(package, version, output_dir)
        except Exception as error:
            errors.append(f"{provider}: {type(error).__name__}: {error}")
            logging.warning("⚠️  %s fallback failed: %s", provider, error)
    raise RuntimeError("all non-browser fallbacks failed: " + "; ".join(errors))
