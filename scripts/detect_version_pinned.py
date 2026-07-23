"""
各アプリ×パッチバンドルに対して CLI の list-versions を叩き、
推奨バージョンが存在する（= Any でない）アプリを検出する。

出力: GITHUB_OUTPUT に version_pinned_apps=["youtube","youtube-music",...] を書き込む

推奨バージョンが存在する = パッチ側が特定バージョンを要求している
  → APK本体が新しくなっても関係ない → APK監視スキップ対象

推奨バージョンなし（Any） = 全バージョン対応
  → APK本体が更新されたら再ビルドが必要 → APK監視対象
"""
import json, os, pathlib, sys, logging
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

from src import utils

TOOLS_DIR   = pathlib.Path("tools")
SOURCES_DIR = pathlib.Path("sources")

def get_source_tool_name(source_name: str) -> str:
    """sources/<source>.json の最初のエントリから tools/ のディレクトリ名を取得"""
    source_file = SOURCES_DIR / f"{source_name}.json"
    if source_file.exists():
        try:
            data = json.loads(source_file.read_text())
            if isinstance(data, list) and data:
                return data[0].get("name", source_name)
        except Exception:
            pass
    return source_name

def find_cli_and_bundle(tool_name: str):
    """tools/<tool_name>/ 以下から CLI jar とパッチバンドルを探す（__main__.py の実装を踏襲）"""
    source_dir = TOOLS_DIR / tool_name
    if not source_dir.exists():
        return None, None

    files = list(source_dir.iterdir())

    # is_morphe 判定（__main__.py と同じロジック。"morphe-cli"文字列に依存すると
    # 上流のリポジトリ改名で誤判定するため、拡張子ベースで判定する）
    is_morphe = any("morphe" in f.name.lower() and f.suffix == ".jar" for f in files)
    if not is_morphe:
        is_morphe = any(f.suffix == ".mpp" for f in files)

    if is_morphe:
        cli = (
            utils.find_file(files, contains="morphe-cli", suffix=".jar", exclude=["dev"])
            or utils.find_file(files, contains="morphe", suffix=".jar")
        )
        bundle = (
            utils.find_file(files, contains="patches", suffix=".mpp")
            or utils.find_file(files, suffix=".mpp")
        )
    else:
        cli = utils.find_file(files, contains="revanced-cli", suffix=".jar")
        bundle = (
            utils.find_file(files, contains="patches", suffix=".rvp")
            or utils.find_file(files, contains="patches", suffix=".mpp")
            or utils.find_file(files, suffix=".mpp")
            or utils.find_file(files, contains="patches", suffix=".jar")
        )

    return cli, bundle

def get_package(app_name: str) -> str | None:
    """apps/*/アプリ.json から package 名を取得"""
    for platform_dir in sorted(pathlib.Path("apps").iterdir()):
        config_path = platform_dir / f"{app_name}.json"
        if config_path.exists():
            try:
                return json.loads(config_path.read_text()).get("package")
            except Exception:
                continue
    return None

# my-patch-config.json からアプリ×ソース一覧を取得（重複排除・順序保持）
with open("my-patch-config.json") as f:
    patch_list = json.load(f)["patch_list"]

app_sources: dict[str, list[str]] = {}
for item in patch_list:
    app_sources.setdefault(item["app_name"], [])
    if item["source"] not in app_sources[item["app_name"]]:
        app_sources[item["app_name"]].append(item["source"])

pinned_apps = []
free_apps   = []

for app_name, sources in app_sources.items():
    package = get_package(app_name)
    if not package:
        logging.warning(f"{app_name}: package 不明 → APK監視対象として扱う")
        free_apps.append(app_name)
        continue

    is_pinned = False
    for source in sources:
        tool_name = get_source_tool_name(source)
        cli, bundle = find_cli_and_bundle(tool_name)

        if not cli or not bundle:
            logging.warning(f"{app_name} ({source}): CLI or bundle 未発見 → スキップ")
            continue

        versions = utils.get_supported_version(package, str(cli), str(bundle))
        if versions:
            logging.info(f"✅ {app_name} ({source}): 推奨バージョンあり → APK監視スキップ")
            is_pinned = True
            break
        else:
            logging.info(f"   {app_name} ({source}): Any（全バージョン対応）→ APK監視対象")

    if is_pinned:
        pinned_apps.append(app_name)
    else:
        free_apps.append(app_name)

logging.info(f"推奨バージョンあり（APK監視スキップ）: {pinned_apps}")
logging.info(f"全バージョン対応（APK監視対象）:       {free_apps}")

github_output = os.environ.get("GITHUB_OUTPUT")
if github_output:
    with open(github_output, "a") as f:
        f.write(f"version_pinned_apps={json.dumps(pinned_apps)}\n")
        f.write(f"free_version_apps={json.dumps(free_apps)}\n")
else:
    print(f"version_pinned_apps={json.dumps(pinned_apps)}")
    print(f"free_version_apps={json.dumps(free_apps)}")
