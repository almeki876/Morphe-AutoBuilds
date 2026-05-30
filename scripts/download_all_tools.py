"""
全 sources/*.json に記載されたツール(CLI/patches)を一括ダウンロードする。
tools/<source_name>/ 以下に配置する。
各ビルドジョブはこのディレクトリをキャッシュから取得して使う。
"""
import json, logging, pathlib, subprocess, sys, time
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s', datefmt='%Y-%m-%d %H:%M:%S')

from src import utils


# patches-list.json のソース別取得URL
PATCHES_LIST_URLS: dict[str, str] = {
    "revanced-anddea": "https://raw.githubusercontent.com/anddea/revanced-patches/refs/heads/dev/patches-list.json",
    "morphe": None,  # リリースアセットから取得
}

SOURCES_DIR = pathlib.Path("sources")
TOOLS_DIR   = pathlib.Path("tools")

def download_asset(url: str, dest: pathlib.Path, retries: int = 3) -> bool:
    for attempt in range(1, retries + 1):
        try:
            result = subprocess.run(
                ["curl", "-fsSL", "--retry", "3", "--retry-delay", "5", url, "-o", str(dest)],
                capture_output=True, text=True
            )
            if result.returncode == 0 and dest.exists() and dest.stat().st_size > 0:
                logging.info(f"  ✅ {dest.name} ({dest.stat().st_size:,} bytes)")
                return True
            logging.warning(f"  ⚠️  attempt {attempt}: curl exit={result.returncode}")
        except Exception as e:
            logging.warning(f"  ⚠️  attempt {attempt}: {e}")
        if attempt < retries:
            time.sleep(10 * attempt)
    logging.error(f"  ❌ failed after {retries} attempts: {url}")
    return False

failures = []

for source_path in sorted(SOURCES_DIR.glob("*.json")):
    source_name = source_path.stem
    if source_name == "github":
        continue  # github.jsonはmorpheと同じファイルを使う

    with source_path.open() as f:
        repos_info = json.load(f)

    if isinstance(repos_info, dict):
        continue  # bundle形式はスキップ

    name = repos_info[0]["name"]
    dest_dir = TOOLS_DIR / name
    dest_dir.mkdir(parents=True, exist_ok=True)

    logging.info(f"\n📦 Downloading tools for source: {name}")

    # SOURCE_TAG_<SOURCE_NAME> 環境変数が渡されていればそのタグを優先する
    # （check-upstream.yml から最新タグが渡される）
    import os as _os
    env_tag_key = f"SOURCE_TAG_{source_name.upper().replace('-', '_')}"
    env_tag = _os.environ.get(env_tag_key, "").strip()

    for repo_info in repos_info[1:]:
        user = repo_info["user"]
        repo = repo_info["repo"]
        # CLIリポジトリ（repo名に"cli"を含む）は常にlatestを使う
        # パッチバンドルにのみ SOURCE_TAG_* を適用する
        is_cli_repo = "cli" in repo.lower()
        tag = repo_info["tag"] if is_cli_repo else (env_tag if env_tag else repo_info["tag"])

        # リトライ付きでリリース情報を取得
        try:
            release = utils.detect_github_release(user, repo, tag)
        except Exception as e:
            logging.error(f"  ❌ Could not fetch release for {user}/{repo}: {e}")
            failures.append(f"{name}: {user}/{repo}")
            continue

        for asset in release.get("assets", []):
            aname = asset["name"]
            if aname.endswith(".asc"):
                continue
            # CLI/patchesファイルのみ対象
            is_cli     = aname.endswith(".jar") and ("cli" in aname.lower())
            is_patches = aname.endswith((".mpp", ".rvp")) or \
                         (aname.endswith(".jar") and "patch" in aname.lower())
            if not (is_cli or is_patches):
                continue

            dest_file = dest_dir / aname
            if dest_file.exists() and dest_file.stat().st_size > 0:
                logging.info(f"  ⏭️  already exists: {aname}")
                continue

            logging.info(f"  ⬇️  {aname}")
            ok = download_asset(asset["browser_download_url"], dest_file)
            if not ok:
                failures.append(f"{name}: {aname}")


    # patches-list.json を別途取得（リリースアセットにない場合はrawから）
    patches_list_url = PATCHES_LIST_URLS.get(name)
    if patches_list_url:
        dest_file = dest_dir / "patches-list.json"
        if dest_file.exists() and dest_file.stat().st_size > 0:
            logging.info(f"  ⏭️  already exists: patches-list.json")
        else:
            logging.info(f"  ⬇️  patches-list.json (from raw)")
            ok = download_asset(patches_list_url, dest_file)
            if not ok:
                failures.append(f"{name}: patches-list.json")

if failures:
    logging.warning(f"\n⚠️  {len(failures)} download(s) failed:")
    for f in failures:
        logging.warning(f"  - {f}")
    sys.exit(1)

logging.info("\n✅ All tools downloaded successfully.")
