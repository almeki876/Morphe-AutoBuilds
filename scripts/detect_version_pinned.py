"""
各アプリ×パッチバンドルに対して CLI の list-versions を叩き、
推奨バージョンが存在する（= Any でない）アプリを検出する。

出力: GITHUB_OUTPUT に以下を書き込む
  version_pinned_apps   = ["youtube","youtube-music",...]  APK監視スキップ対象
  free_version_apps     = [...]                            APK監視対象
  lookup_failed_apps    = [...]                            判定に失敗したアプリ（free 扱い）

推奨バージョンが存在する = パッチ側が特定バージョンを要求している
  → APK本体が新しくなっても関係ない → APK監視スキップ対象

推奨バージョンなし（Any） = 全バージョン対応
  → APK本体が更新されたら再ビルドが必要 → APK監視対象

堅牢性に関する設計:
  * 1アプリ/1ソースの判定失敗が他のアプリの結果を巻き込まない
    （以前は例外でスクリプト全体が落ち、判定済みの pinned が全て失われ、
      全アプリが APK 監視対象になってタイムアウトの一因になった）
  * universal パッチなどバージョン制約を持たないバンドルは Any として扱う
  * 出力は例外・中断時でも必ず finally で書き出す
"""
import json, os, pathlib, signal, sys, logging
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

from src import utils

TOOLS_DIR   = pathlib.Path("tools")
SOURCES_DIR = pathlib.Path("sources")
APPS_DIR    = pathlib.Path("apps")

# 「バージョン制約を判定できなかった」ことを表す例外。無ければ汎用 Exception で代用。
LookupErrorType = getattr(utils, "SupportedVersionLookupError", Exception)


def get_source_tool_name(source_name: str) -> str:
    """sources/<source>.json の最初のエントリから tools/ のディレクトリ名を取得"""
    source_file = SOURCES_DIR / f"{source_name}.json"
    if source_file.exists():
        try:
            data = json.loads(source_file.read_text(encoding="utf-8"))
            if isinstance(data, list) and data:
                return data[0].get("name", source_name)
        except Exception as e:
            logging.warning(f"{source_file} の解析に失敗: {type(e).__name__}: {e}")
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
    if not APPS_DIR.is_dir():
        return None
    for platform_dir in sorted(APPS_DIR.iterdir()):
        config_path = platform_dir / f"{app_name}.json"
        if config_path.exists():
            try:
                return json.loads(config_path.read_text(encoding="utf-8")).get("package")
            except Exception:
                continue
    return None


def classify_app(app_name: str, sources: list[str]) -> tuple[str, bool]:
    """
    アプリを判定し ("pinned" | "free", lookup_failed) を返す。この関数は例外を投げない。

    - いずれかのソースで推奨バージョンが取れれば pinned
    - 判定不能（universal パッチ・CLI異常など）は free（= APK監視対象）扱い。
      監視が増えるだけで、ビルドが誤って止まる方向の失敗にはならない
    """
    package = get_package(app_name)
    if not package:
        logging.warning(f"{app_name}: package 不明 → APK監視対象として扱う")
        return "free", True

    lookup_failed = False
    for source in sources:
        try:
            tool_name = get_source_tool_name(source)
            cli, bundle = find_cli_and_bundle(tool_name)
            if not cli or not bundle:
                logging.warning(f"{app_name} ({source}): CLI or bundle 未発見 → スキップ")
                lookup_failed = True
                continue

            versions = utils.get_supported_version(package, str(cli), str(bundle))
        except LookupErrorType as e:
            # バージョン制約なし（universal 等）または出力を解釈できなかった
            logging.warning(
                f"{app_name} ({source}): 推奨バージョン判定不可 ({e}) → Any として扱う"
            )
            lookup_failed = True
            continue
        except Exception as e:
            logging.warning(
                f"{app_name} ({source}): 想定外のエラー {type(e).__name__}: {e} → Any として扱う"
            )
            lookup_failed = True
            continue

        if versions:
            logging.info(f"✅ {app_name} ({source}): 推奨バージョンあり → APK監視スキップ")
            return "pinned", False
        logging.info(f"   {app_name} ({source}): Any（全バージョン対応）→ APK監視対象")

    return "free", lookup_failed


def write_outputs(pinned_apps, free_apps, failed_apps):
    values = {
        "version_pinned_apps": pinned_apps,
        "free_version_apps": free_apps,
        "lookup_failed_apps": failed_apps,
    }
    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        with open(github_output, "a", encoding="utf-8") as f:
            for key, val in values.items():
                f.write(f"{key}={json.dumps(val, ensure_ascii=False)}\n")
    else:
        for key, val in values.items():
            print(f"{key}={json.dumps(val, ensure_ascii=False)}")


def main() -> int:
    # ランナーからの停止要求（timeout / cancel）でも finally を通して出力を残す
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))

    with open("my-patch-config.json", encoding="utf-8") as f:
        patch_list = json.load(f)["patch_list"]

    app_sources: dict[str, list[str]] = {}
    for item in patch_list:
        srcs = app_sources.setdefault(item["app_name"], [])
        if item["source"] not in srcs:
            srcs.append(item["source"])

    pinned_apps: list[str] = []
    free_apps: list[str] = []
    failed_apps: list[str] = []

    try:
        for app_name, sources in app_sources.items():
            kind, failed = classify_app(app_name, sources)
            (pinned_apps if kind == "pinned" else free_apps).append(app_name)
            if failed and kind != "pinned":
                failed_apps.append(app_name)
    finally:
        # 未処理のアプリ（中断時）は free 側に入れて「監視漏れ」を防ぐ
        done = set(pinned_apps) | set(free_apps)
        for app_name in app_sources:
            if app_name not in done:
                free_apps.append(app_name)
                failed_apps.append(app_name)

        logging.info(f"推奨バージョンあり（APK監視スキップ）: {pinned_apps}")
        logging.info(f"全バージョン対応（APK監視対象）:       {free_apps}")
        if failed_apps:
            print(f"::warning::推奨バージョン判定に失敗し Any 扱いにしたアプリ: {failed_apps}")
        write_outputs(pinned_apps, free_apps, failed_apps)
    return 0


if __name__ == "__main__":
    sys.exit(main())
