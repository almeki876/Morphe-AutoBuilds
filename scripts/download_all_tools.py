"""
全 sources/*.json に記載されたツール(CLI/patches)を一括ダウンロードする。
tools/<source_name>/ 以下に配置する。
各ビルドジョブはこのディレクトリをキャッシュから取得して使う。
"""
import json, logging, os, pathlib, subprocess, sys, time
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

def download_asset(url: str, dest: pathlib.Path, retries: int = 3, token: str = "") -> bool:
    for attempt in range(1, retries + 1):
        try:
            cmd = ["curl", "-fsSL", "--retry", "3", "--retry-delay", "5"]
            if token:
                cmd += ["-H", f"Authorization: token {token}"]
            cmd += [url, "-o", str(dest)]
            result = subprocess.run(cmd, capture_output=True, text=True)
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

# ── yuzu: patches-1.0.rvp を固定URLから直接ダウンロード ─────────────────────
# PAT シークレット (secrets.PAT) を PAT 環境変数として受け取り、
# プライベートリリースへのアクセスに使用する。
_yuzu_pat = os.environ.get("PAT", "").strip()
_yuzu_patches_url = (
    "https://github.com/matchadaisuke/morphe-patches/releases/download/patche/patches-1.0.rvp"
)
_yuzu_dest_dir = TOOLS_DIR / "yuzu"
_yuzu_dest_dir.mkdir(parents=True, exist_ok=True)
_yuzu_dest_file = _yuzu_dest_dir / "patches-1.0.rvp"

logging.info("\n📦 Downloading yuzu patches from fixed URL")
logging.info(f"  ⬇️  patches-1.0.rvp")
if not download_asset(_yuzu_patches_url, _yuzu_dest_file, token=_yuzu_pat):
    failures.append("yuzu: patches-1.0.rvp")

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
    # （build.yml の download-tools ジョブが resolve-tags で確定させた具体的なタグを渡す）
    # "latest" や空文字が渡された場合は detect_github_release 側で解決される。
    env_tag_key = f"SOURCE_TAG_{source_name.upper().replace('-', '_')}"
    env_tag = os.environ.get(env_tag_key, "").strip()
    logging.info(f"  SOURCE_TAG env ({env_tag_key}): {env_tag or '(not set, will use sources json)'}")

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
            # キャッシュミス時にのみこのスクリプトが実行される。
            # tools/ ディレクトリはキャッシュから復元されていないため、
            # 既存ファイルチェックは不要（ほぼ常に空）。
            # ただし万一残骸ファイルがあっても上書きして正しいバージョンを保証する。
            if dest_file.exists() and dest_file.stat().st_size > 0:
                logging.info(f"  ⏭️  already exists (unexpected on cache miss): {aname}")
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
