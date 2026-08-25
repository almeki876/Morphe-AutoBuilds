"""
全 sources/*.json に記載されたツール(CLI/patches)を一括ダウンロードする。
tools/<source_name>/ 以下に配置する。
各ビルドジョブはこのディレクトリをキャッシュから取得して使う。
"""
import json
import logging
import os
import pathlib
import subprocess
import sys
import tempfile
import time
import urllib.parse

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

# リリースアセットに metadata がないソースの patches-list.json パス。
# URL は必ず、実際に解決・取得した bundle の release tag から組み立てる。
PATCHES_LIST_FILES: dict[str, str] = {
    "revanced-anddea": "patches-list.json",
}

SOURCES_DIR = pathlib.Path("sources")
TOOLS_DIR   = pathlib.Path("tools")
PATCH_CONFIG_PATH = pathlib.Path("my-patch-config.json")


def tagged_patches_list_url(
    user: str,
    repo: str,
    release_tag: str,
    metadata_path: str,
) -> str:
    """Build a raw URL pinned to the same release tag as the patch bundle."""
    release_tag = release_tag.strip()
    metadata_path = metadata_path.strip("/")
    if not release_tag:
        raise ValueError("A resolved patch bundle release tag is required")
    if not metadata_path or any(part in ("", ".", "..") for part in metadata_path.split("/")):
        raise ValueError(f"Invalid patches-list path: {metadata_path!r}")

    owner = urllib.parse.quote(user.strip(), safe="")
    repository = urllib.parse.quote(repo.strip(), safe="")
    tag = urllib.parse.quote(release_tag, safe="")
    path = "/".join(
        urllib.parse.quote(part, safe="") for part in metadata_path.split("/")
    )
    return (
        f"https://raw.githubusercontent.com/{owner}/{repository}/"
        f"refs/tags/{tag}/{path}"
    )


def download_asset(url: str, dest: pathlib.Path, retries: int = 3, token: str = "") -> bool:
    """Download an asset atomically so a failed attempt never leaves a valid-looking file."""
    for attempt in range(1, retries + 1):
        part_file: pathlib.Path | None = None
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            fd, part_name = tempfile.mkstemp(
                prefix=f".{dest.name}.", suffix=".part", dir=dest.parent
            )
            os.close(fd)
            part_file = pathlib.Path(part_name)
            cmd = ["curl", "-fsSL", "--retry", "3", "--retry-delay", "5"]
            if token:
                cmd += ["-H", f"Authorization: token {token}"]
            cmd += [url, "-o", str(part_file)]
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode == 0 and part_file.stat().st_size > 0:
                part_file.replace(dest)
                logging.info(f"  ✅ {dest.name} ({dest.stat().st_size:,} bytes)")
                return True
            detail = result.stderr.strip()
            logging.warning(
                f"  ⚠️  attempt {attempt}: curl exit={result.returncode}"
                + (f" stderr={detail}" if detail else "")
            )
        except Exception as e:
            logging.warning(f"  ⚠️  attempt {attempt}: {e}")
        finally:
            if part_file is not None:
                part_file.unlink(missing_ok=True)
        if attempt < retries:
            time.sleep(10 * attempt)
    logging.error(f"  ❌ failed after {retries} attempts: {url}")
    return False

def _is_permanent_github_error(message: str) -> bool:
    """Return whether retrying the same GitHub credential cannot help."""
    normalized = message.lower()
    return any(
        marker in normalized
        for marker in (
            "401 unauthorized",
            "bad credentials",
            "http 401",
            "resource not accessible by integration",
            "404 not found",
            "http 404",
            "release not found",
        )
    )


def _same_repository(first: str, second: str) -> bool:
    """Compare GitHub owner/repository slugs without case sensitivity."""
    return bool(first.strip()) and first.strip().casefold() == second.strip().casefold()


def _credential_candidates(primary_token: str, fallback_token: str) -> list[tuple[str, str]]:
    candidates: list[tuple[str, str]] = []
    seen: set[str] = set()
    for label, token in (("PAT", primary_token), ("GITHUB_TOKEN", fallback_token)):
        token = token.strip()
        if token and token not in seen:
            candidates.append((label, token))
            seen.add(token)
    return candidates


def download_asset_gh(
    repo: str,
    tag: str,
    filename: str,
    dest: pathlib.Path,
    token: str,
    fallback_token: str = "",
    retries: int = 3,
) -> bool:
    """Download a private release asset atomically with actionable auth failures."""
    credentials = _credential_candidates(token, fallback_token)
    if not credentials:
        logging.error(
            "  ❌ No GitHub credential is configured. Set the repository Actions "
            "secret PAT to a token that can read the private repository."
        )
        return False

    dest.parent.mkdir(parents=True, exist_ok=True)
    for credential_label, credential in credentials:
        for attempt in range(1, retries + 1):
            try:
                env = {**os.environ, "GH_TOKEN": credential}
                verify_cmd = [
                    "gh",
                    "release",
                    "view",
                    tag,
                    "--repo",
                    repo,
                    "--json",
                    "assets",
                    "--jq",
                    ".assets[].name",
                ]
                verify_result = subprocess.run(
                    verify_cmd, capture_output=True, text=True, env=env
                )
                verify_detail = "\n".join(
                    part.strip()
                    for part in (verify_result.stderr, verify_result.stdout)
                    if part.strip()
                )
                if verify_result.returncode != 0:
                    if _is_permanent_github_error(verify_detail):
                        logging.error(
                            f"  ❌ {credential_label} cannot access "
                            f"{repo}@{tag}: {verify_detail or 'access denied'}"
                        )
                        if credential_label == "PAT":
                            logging.error(
                                "     Fine-grained PAT requirements: resource "
                                f"owner '{repo.split('/', 1)[0]}', selected "
                                f"repository '{repo}', Contents: Read-only."
                            )
                        break
                    logging.warning(
                        f"  ⚠️  {credential_label} preflight attempt {attempt}: "
                        f"gh exit={verify_result.returncode}"
                        + (f" stderr={verify_detail}" if verify_detail else "")
                    )
                    if attempt < retries:
                        time.sleep(10 * attempt)
                    continue

                available_assets = {
                    name.strip()
                    for name in verify_result.stdout.splitlines()
                    if name.strip()
                }
                if filename not in available_assets:
                    available = ", ".join(sorted(available_assets)) or "(none)"
                    logging.error(
                        f"  ❌ Release {repo}@{tag} is accessible, but asset "
                        f"'{filename}' does not exist. Available assets: {available}"
                    )
                    return False

                with tempfile.TemporaryDirectory(
                    prefix=f".{dest.name}.", dir=dest.parent
                ) as temp_dir:
                    cmd = [
                        "gh", "release", "download", tag,
                        "--repo", repo,
                        "--pattern", filename,
                        "--dir", temp_dir,
                        "--clobber",
                    ]
                    result = subprocess.run(
                        cmd, capture_output=True, text=True, env=env
                    )
                    downloaded = pathlib.Path(temp_dir) / filename
                    if (
                        result.returncode == 0
                        and downloaded.exists()
                        and downloaded.stat().st_size > 0
                    ):
                        downloaded.replace(dest)
                        logging.info(
                            f"  ✅ {dest.name} ({dest.stat().st_size:,} bytes)"
                        )
                        return True

                    detail = "\n".join(
                        part.strip()
                        for part in (result.stderr, result.stdout)
                        if part.strip()
                    )
                    if _is_permanent_github_error(detail):
                        logging.error(
                            f"  ❌ {credential_label} was rejected for {repo}; "
                            "this authentication/access/not-found error will "
                            "not succeed by retrying the same credential."
                        )
                        break
                    logging.warning(
                        f"  ⚠️  {credential_label} attempt {attempt}: "
                        f"gh exit={result.returncode}"
                        + (f" stderr={detail}" if detail else "")
                    )
            except Exception as e:
                logging.warning(
                    f"  ⚠️  {credential_label} attempt {attempt}: {e}"
                )
            if attempt < retries:
                time.sleep(10 * attempt)

    logging.error(
        f"  ❌ Could not download {repo}@{tag}/{filename}. Verify the release "
        "tag and asset name; for a 401 Bad credentials response, rotate the "
        "Actions secret PAT and grant it read access to this private repository."
    )
    return False

def main() -> int:
    # Import only for the full download command. Keeping the transport helpers
    # dependency-free makes authentication/error handling independently testable.
    from src import utils

    failures = []
    try:
        source_tags = json.loads(os.environ.get("SOURCE_TAGS_JSON", "{}"))
    except json.JSONDecodeError as error:
        raise ValueError("SOURCE_TAGS_JSON must be valid JSON") from error
    if not isinstance(source_tags, dict):
        raise ValueError("SOURCE_TAGS_JSON must be a JSON object")

    for source_path in sorted(SOURCES_DIR.glob("*.json")):
        source_name = source_path.stem
        if source_name == "github":
            continue  # github.jsonはmorpheと同じファイルを使う

        with source_path.open(encoding="utf-8") as source_file:
            repos_info = json.load(source_file)

        if isinstance(repos_info, dict):
            continue  # bundle形式はスキップ

        name = repos_info[0]["name"]
        dest_dir = TOOLS_DIR / name
        dest_dir.mkdir(parents=True, exist_ok=True)

        logging.info(f"\n📦 Downloading tools for source: {name}")

        # SOURCE_TAG_<SOURCE_NAME> 環境変数が渡されていればそのタグを優先する
        env_tag_key = f"SOURCE_TAG_{source_name.upper().replace('-', '_')}"
        env_tag = str(source_tags.get(name) or os.environ.get(env_tag_key, "")).strip()
        logging.info(
            f"  SOURCE_TAG env ({env_tag_key}): "
            f"{env_tag or '(not set, will use sources json)'}"
        )

        resolved_patch_release: tuple[str, str, str] | None = None
        for repo_idx, repo_info in enumerate(repos_info[1:]):
            user = repo_info["user"]
            repo = repo_info["repo"]
            # 先頭のリポジトリは CLI、それ以降はパッチバンドル。
            is_cli_repo = repo_idx == 0
            tag = (
                repo_info["tag"]
                if is_cli_repo
                else (env_tag if env_tag else repo_info["tag"])
            )

            try:
                if repo_info.get("gitlab"):
                    release = utils.detect_gitlab_release(user, repo, tag)
                else:
                    release = utils.detect_github_release(user, repo, tag)
            except Exception as e:
                logging.error(f"  ❌ Could not fetch release for {user}/{repo}: {e}")
                failures.append(f"{name}: {user}/{repo}")
                continue

            if not is_cli_repo:
                resolved_tag = str(release.get("tag_name") or "").strip()
                if not resolved_tag:
                    logging.error(
                        f"  ❌ Patch release for {user}/{repo}@{tag} did not "
                        "provide its resolved tag; refusing unpinned metadata"
                    )
                    failures.append(f"{name}: unresolved patch release tag")
                else:
                    resolved_patch_release = (user, repo, resolved_tag)

            all_assets = release.get("assets", [])
            matched_any = False

            for asset in all_assets:
                aname = asset["name"]
                if aname.endswith((".asc", ".sig", ".sha256", ".sha512", ".md5")):
                    continue

                if is_cli_repo:
                    is_wanted = aname.endswith(".jar") and not (
                        "sources" in aname.lower() or "javadoc" in aname.lower()
                    )
                else:
                    is_wanted = aname.endswith((".mpp", ".rvp")) or (
                        aname.endswith(".jar") and "patch" in aname.lower()
                    )
                if not is_wanted:
                    continue

                matched_any = True
                dest_file = dest_dir / aname
                if dest_file.exists() and dest_file.stat().st_size > 0:
                    logging.info(
                        f"  ⏭️  already exists (unexpected on cache miss): {aname}"
                    )
                    continue

                logging.info(f"  ⬇️  {aname}")
                if not download_asset(asset["browser_download_url"], dest_file):
                    failures.append(f"{name}: {aname}")

            if not matched_any:
                role = "CLI" if is_cli_repo else "patches"
                available = [a["name"] for a in all_assets] or [
                    "(no assets in release)"
                ]
                logging.error(
                    f"  ❌ No {role} asset matched for {user}/{repo}@{tag}. "
                    f"Available assets: {available}"
                )
                failures.append(
                    f"{name}: no {role} asset found in {user}/{repo}@{tag}"
                )

        patches_list_file = PATCHES_LIST_FILES.get(name)
        if patches_list_file:
            if resolved_patch_release is None:
                logging.error(
                    "  ❌ Cannot download patches-list.json without the resolved "
                    "patch bundle release tag"
                )
                failures.append(f"{name}: patches-list.json release tag")
                continue

            patch_user, patch_repo, patch_tag = resolved_patch_release
            patches_list_url = tagged_patches_list_url(
                patch_user,
                patch_repo,
                patch_tag,
                patches_list_file,
            )
            dest_file = dest_dir / "patches-list.json"
            if dest_file.exists() and dest_file.stat().st_size > 0:
                logging.info("  ⏭️  already exists: patches-list.json")
            else:
                logging.info(
                    f"  ⬇️  patches-list.json (from {patch_user}/{patch_repo}@{patch_tag})"
                )
                if not download_asset(patches_list_url, dest_file):
                    failures.append(f"{name}: patches-list.json")

    if failures:
        logging.warning(f"\n⚠️  {len(failures)} download(s) failed:")
        for failure in failures:
            logging.warning(f"  - {failure}")
        return 1

    logging.info("\n✅ All tools downloaded successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
