import json
import logging
from pathlib import Path
from src import (
    session,
    utils,
    apkpure,
    uptodown,
    aptoide,
    apkmirror,
    github,
)

def download_resource(url: str, name: str = None, retries: int = 3, referer: str = None) -> Path:
    last_err = None
    headers = {}
    if referer:
        headers["Referer"] = referer
    # APKMirrorのr2.cloudflarestorage URLはRefererが必須
    if not referer and "apkmirror.com" in url:
        headers["Referer"] = "https://www.apkmirror.com/"
    for attempt in range(1, retries + 1):
        try:
            res = session.get(url, stream=True, headers=headers if headers else None)
            res.raise_for_status()
            break
        except Exception as e:
            last_err = e
            if attempt < retries:
                import time
                wait = attempt * 5
                logging.warning(f"download_resource: attempt {attempt} failed ({e}), retrying in {wait}s...")
                time.sleep(wait)
            else:
                raise last_err
    final_url = res.url

    if not name:
        name = utils.extract_filename(res, fallback_url=final_url)

    filepath = Path(name)
    total_size = int(res.headers.get('content-length', 0))
    downloaded_size = 0

    with filepath.open("wb") as file:
        for chunk in res.iter_content(chunk_size=8192):
            if chunk:
                file.write(chunk)
                downloaded_size += len(chunk)

    logging.info(
        f"URL: {final_url} [{downloaded_size}/{total_size}] -> \"{filepath}\" [1]"
    )

    return filepath

def download_required(source: str) -> tuple[list[Path], str]:
    source_path = Path("sources") / f"{source}.json"
    with source_path.open() as json_file:
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
    with session.get(bundle_url) as res:
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

def download_platform(app_name: str, platform: str, cli: str, patches: str, arch: str = None) -> tuple[Path | None, str | None]:
    config_path = Path("apps") / platform / f"{app_name}.json"

    # jsonがない = このプラットフォームは未設定なので静かにスキップ
    if not config_path.exists():
        logging.info(f"⏭️  {platform}: no config for {app_name}, skipping")
        return None, None

    try:
        with config_path.open() as json_file:
            config = json.load(json_file)
        
        if arch:
            config['arch'] = arch

        # Support direct_url: skip version resolution and download directly
        direct_url = config.get("direct_url")
        if direct_url:
            logging.info(f"🔗 {platform}: using direct_url for {app_name}")
            try:
                filepath = download_resource(direct_url)
                # Try to resolve version: first check pinned version in config,
                # then try the current platform's get_latest_version,
                # then fall back to other platforms (apkmirror, apkpure, uptodown, aptoide)
                version = config.get("version") or None
                if not version:
                    try:
                        platform_mod = globals().get(platform)
                        if platform_mod and hasattr(platform_mod, "get_latest_version"):
                            version = platform_mod.get_latest_version(app_name, config)
                    except Exception:
                        pass
                if not version:
                    fallback_platforms = [p for p in ["apkmirror", "apkpure", "uptodown", "aptoide"] if p != platform]
                    for fb_platform in fallback_platforms:
                        fb_config_path = Path("apps") / fb_platform / f"{app_name}.json"
                        if not fb_config_path.exists():
                            continue
                        try:
                            import json as _json
                            with fb_config_path.open() as _f:
                                fb_config = _json.load(_f)
                            fb_mod = globals().get(fb_platform)
                            if fb_mod and hasattr(fb_mod, "get_latest_version"):
                                version = fb_mod.get_latest_version(app_name, fb_config)
                                if version:
                                    logging.info(f"🔍 {platform}: resolved version {version} for {app_name} via {fb_platform} fallback")
                                    break
                        except Exception as e:
                            logging.debug(f"direct_url version fallback via {fb_platform} failed: {e}")
                            continue
                version = version or "latest"
                logging.info(f"✅ {platform}: downloaded {app_name} via direct_url -> {filepath.name} (version={version})")
                return filepath, version
            except Exception as e:
                logging.error(f"❌ {platform}: direct_url download failed for {app_name}: {type(e).__name__}: {e}")
                return None, None

        version = config.get("version") or None
        if not version:
            if platform == "github":
                # GitHub releases carry the version in the tag — skip CLI invocation
                try:
                    version = globals()["github"].get_latest_version(app_name, config)
                except Exception as e:
                    logging.error(f"❌ github: get_latest_version failed for {app_name}: {e}")
                    return None, None
            else:
                version = utils.get_supported_version(config['package'], cli, patches)
        platform_module = globals()[platform]

        if not version:
            logging.warning(f"⚠️  {platform}: CLI/patch version lookup failed for {app_name}, falling back to get_latest_version")
            try:
                version = platform_module.get_latest_version(app_name, config)
            except Exception as e:
                logging.error(f"❌ {platform}: get_latest_version failed for {app_name}: {type(e).__name__}: {e}")
                return None, None

        if not version:
            logging.error(f"❌ {platform}: could not resolve any version for {app_name}")
            return None, None

        logging.info(f"🔍 {platform}: resolved version {version} for {app_name}")

        try:
            download_link = platform_module.get_download_link(version, app_name, config)
        except Exception as e:
            logging.error(f"❌ {platform}: get_download_link failed for {app_name} v{version}: {type(e).__name__}: {e}")
            return None, None

        if not download_link:
            logging.error(f"❌ {platform}: no download link found for {app_name} v{version}")
            return None, None

        # APKMirrorはRefererが必要（ないと403）
        referer = "https://www.apkmirror.com/" if platform == "apkmirror" else None
        filepath = download_resource(download_link, referer=referer)
        logging.info(f"✅ {platform}: downloaded {app_name} v{version} -> {filepath.name}")
        return filepath, version

    except Exception as e:
        logging.error(f"❌ {platform}: unexpected error for {app_name}: {type(e).__name__}: {e}")
        return None, None

def download_github(app_name: str, cli: str, patches: str, arch: str = None) -> tuple[Path | None, str | None]:
    return download_platform(app_name, "github", cli, patches, arch)

def download_apkmirror(app_name: str, cli: str, patches: str, arch: str = None) -> tuple[Path | None, str | None]:
    return download_platform(app_name, "apkmirror", cli, patches, arch)

def download_apkpure(app_name: str, cli: str, patches: str, arch: str = None) -> tuple[Path | None, str | None]:
    return download_platform(app_name, "apkpure", cli, patches, arch)

def download_aptoide(app_name: str, cli: str, patches: str, arch: str = None) -> tuple[Path | None, str | None]:
    return download_platform(app_name, "aptoide", cli, patches, arch)

def download_uptodown(app_name: str, cli: str, patches: str, arch: str = None) -> tuple[Path | None, str | None]:
    return download_platform(app_name, "uptodown", cli, patches, arch)

def download_apkeditor() -> Path:
    release = utils.detect_github_release("REAndroid", "APKEditor", "latest")
    for asset in release["assets"]:
        if asset["name"].startswith("APKEditor") and asset["name"].endswith(".jar"):
            return download_resource(asset["browser_download_url"])
    raise RuntimeError("APKEditor .jar file not found in the latest release")
