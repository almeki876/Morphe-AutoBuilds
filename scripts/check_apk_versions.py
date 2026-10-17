"""
監視対象アプリの最新 APK バージョンを取得し、前回(last-tags.json)から変化したものを検出する。

取得順序: Google Play (メタデータのみ) → providers.download_priority(app) のミラー群

堅牢性に関する設計:
  * Google Play が使えないと判明したら、その実行中は Google Play を試行しない
    - PlayUnavailableError（dispenser 起動不可・認証不可など）: 即座に断念
    - PlayLookupError（個別の取得失敗）: 連続 N 回で断念
    - identity が空（パッケージ未掲載など）: Google Play 自体は健全なので数えない
    （dispenser 起動失敗は1回あたり約1分かかり、アプリ数だけ繰り返すと
      ジョブの timeout-minutes に達するため）
  * ステップ全体に締め切りを設け、超過分は「解決失敗」として記録して正常終了へ向かう
    （タイムアウトで強制キャンセルされると GITHUB_OUTPUT もヘルスレポートも失われる）
  * 出力・レポートは finally / SIGTERM でも必ず書き出す
  * 1アプリの想定外エラーが他のアプリの判定を止めない

環境変数:
  VERSION_PINNED_APPS                    JSON配列。APK監視をスキップするアプリ
  APK_CHECK_DEADLINE_SECONDS             全体の締め切り（既定 900）
  APK_PLAY_MAX_CONSECUTIVE_FAILURES      個別取得失敗で Google Play を断念する連続回数（既定 3）
  （src/google_play_metadata.py 側: GPLAYDL_MAX_INFRA_FAILURES,
    GPLAYDL_DISPENSER_START_TIMEOUT, GPLAYDL_LOOKUP_TIMEOUT）
"""
import json, os, sys, logging, pathlib, signal, time
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
logging.basicConfig(level=logging.WARNING)
# dispenser の進捗（clone/build/起動時間・準備完了）を CI ログに残す
logging.getLogger("src.local_gplaydl_dispenser").setLevel(logging.INFO)

START = time.monotonic()

# stdout がパイプだとブロックバッファされ、タイムアウトで強制終了されるとログが
# 消える（実際に PLAY:/FALLBACK: 行が一切残らなかった）。行バッファにして残す。
try:
    sys.stdout.reconfigure(line_buffering=True)
    sys.stderr.reconfigure(line_buffering=True)
except Exception:
    pass


def _env_int(name: str, default: int) -> int:
    try:
        value = int(os.environ.get(name, "") or default)
        return value if value > 0 else default
    except ValueError:
        return default


DEADLINE_SECONDS = _env_int("APK_CHECK_DEADLINE_SECONDS", 900)
PLAY_MAX_FAILURES = _env_int("APK_PLAY_MAX_CONSECUTIVE_FAILURES", 3)


def load_last_tags() -> dict:
    if not os.path.exists("last-tags.json"):
        return {}
    try:
        with open("last-tags.json", encoding="utf-8") as f:
            content = f.read().strip()
        if content:
            data = json.loads(content)
            return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, ValueError) as e:
        logging.warning("last-tags.json is empty or corrupt, treating as {}: %s", e)
    return {}


def load_version_pinned() -> set:
    raw = os.environ.get("VERSION_PINNED_APPS", "[]") or "[]"
    try:
        return set(json.loads(raw))
    except Exception:
        return set()


def main() -> int:
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))

    last = load_last_tags()

    with open("my-patch-config.json", encoding="utf-8") as f:
        patch_list = json.load(f)["patch_list"]

    # Apps with an explicit patch-supported version are intentionally excluded from
    # latest-APK monitoring. Patch compatibility is authoritative: a newer Play APK
    # must never override an explicitly required patch version.
    version_pinned = load_version_pinned()
    if version_pinned:
        logging.warning("APK監視スキップ対象（推奨バージョン固定）: %s", sorted(version_pinned))
    else:
        # 空の場合は「全アプリ監視」になる。detect_version_pinned の失敗が
        # 原因のことが多いので、気づけるよう目立たせる。
        print("::warning::VERSION_PINNED_APPS が空です。全アプリを APK 監視対象として処理します"
              "（Detect version-pinned apps の結果を確認してください）")

    seen, apps = set(), []
    for item in patch_list:
        name = item["app_name"]
        if name not in seen and name not in version_pinned:
            seen.add(name)
            apps.append(name)

    from src import google_play_metadata, providers, utils
    from src.versioning import canonical_version

    any_apk_updated = False
    apk_updated_apps: list[str] = []
    resolution_failures: dict[str, list[str]] = {}
    unchecked: list[str] = []
    results: dict[str, bool] = {}          # app -> updated?
    state = {"play_failures": 0, "play_disabled": False, "play_reason": ""}
    PlayUnavailableError = google_play_metadata.PlayUnavailableError

    def disable_play(reason: str):
        if not state["play_disabled"]:
            state["play_disabled"] = True
            state["play_reason"] = reason
            print(f"::warning::Google Play をこの実行では以降スキップします: {reason}")

    def safe_log(error, limit=300):
        try:
            return utils.safe_text_for_log(error, limit)
        except Exception:
            return str(error)[:limit]

    def resolve(app: str):
        """(version | None, provider_errors) を返す。想定外の例外も errors に格納する。"""
        cur = None
        errors: list[str] = []
        package = providers.configured_package(app)

        # Google Play is the freshest source for an unrestricted/current release.
        # This is metadata-only: no APK bytes are downloaded. If Play is unavailable
        # (credentials, region, device profile, or temporary outage), keep the
        # existing provider chain as a resilient fallback.
        if package and state["play_disabled"]:
            errors.append(f"google-play: skipped (disabled: {state['play_reason']})")
        elif package:
            try:
                identity = google_play_metadata.current_release_identity(
                    package, strict=True
                )
                state["play_failures"] = 0      # Google Play 自体は応答している
                if identity is not None:
                    cur = canonical_version(identity.name)
                    print(f"PLAY: {app}: current={identity.name} versionCode={identity.code}")
                else:
                    errors.append("google-play: returned no current identity")
            except PlayUnavailableError as error:
                errors.append(f"google-play: {type(error).__name__}: {safe_log(error)}")
                disable_play(safe_log(error, 200))
            except Exception as error:
                state["play_failures"] += 1
                errors.append(f"google-play: {type(error).__name__}: {safe_log(error)}")
                if state["play_failures"] >= PLAY_MAX_FAILURES:
                    disable_play(f"{state['play_failures']} consecutive lookup failures")

        if cur is None:
            if package:
                print(f"FALLBACK: {app}: Google Play failed; trying configured providers")
            for platform in providers.download_priority(app):
                if time.monotonic() - START > DEADLINE_SECONDS:
                    errors.append("deadline exceeded while trying mirrors")
                    break
                try:
                    config = providers.load_config(app, platform)
                    if config is None:
                        errors.append(f"{platform}: no configuration")
                        continue
                    ver = providers.MODULES[platform].get_latest_version(app, config)
                    if ver:
                        cur = canonical_version(ver)
                        break
                    errors.append(f"{platform}: returned no version")
                except Exception as error:
                    errors.append(f"{platform}: {type(error).__name__}: {safe_log(error)}")
        return cur, errors

    try:
        for app in apps:
            if time.monotonic() - START > DEADLINE_SECONDS:
                unchecked.append(app)
                resolution_failures[app] = [f"deadline ({DEADLINE_SECONDS}s) exceeded; not checked"]
                continue

            try:
                cur, errors = resolve(app)
            except Exception as error:   # resolve 自体の想定外エラーでも継続
                cur, errors = None, [f"unexpected: {type(error).__name__}: {safe_log(error)}"]

            if cur is None:
                print(f"WARNING: {app}: could not resolve APK version, skipping")
                resolution_failures[app] = errors
                results[app] = False
                continue

            prev = last.get(f"apk_{app}", "")
            if cur != prev:
                print(f"UPDATED: {app} APK updated: {prev!r} -> {cur!r}")
                any_apk_updated = True
                apk_updated_apps.append(app)
                results[app] = True
            else:
                print(f"UNCHANGED: {app} APK unchanged: {cur}")
                results[app] = False
    finally:
        write_outputs(apps, results, unchecked, any_apk_updated, apk_updated_apps,
                      resolution_failures,
                      (state["play_reason"] or True) if state["play_disabled"] else False)

    return 1 if resolution_failures else 0


def write_outputs(apps, results, unchecked, any_apk_updated, apk_updated_apps,
                  resolution_failures, play_disabled):
    github_output = os.environ.get("GITHUB_OUTPUT")
    lines = []
    for app in apps:
        lines.append(f"apkver_{app}={str(results.get(app, False)).lower()}")
    lines += [
        f"any_apk_updated={str(any_apk_updated).lower()}",
        f"apk_updated_apps={json.dumps(apk_updated_apps)}",
        f"updated_apps={','.join(apk_updated_apps)}",
        f"apk_version_health_ok={str(not resolution_failures).lower()}",
        f"apk_version_failed_apps={json.dumps(sorted(resolution_failures))}",
    ]
    if github_output:
        with open(github_output, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    else:
        print("\n".join(lines))

    report = ["## APK version discovery", ""]
    if play_disabled:
        report += [
            "> ⚠️ Google Play が利用不可と判断されたため、以降はミラーのみで確認しました。"
            + (f" 理由: `{play_disabled}`" if isinstance(play_disabled, str) else ""),
            "",
        ]
    if unchecked:
        report += [f"> ⚠️ 締め切り超過で未確認: {len(unchecked)} 件（{', '.join(unchecked)}）", ""]
    if resolution_failures:
        report.append(
            "The following apps had no working Google Play or mirror version source. "
            "Existing saved versions were preserved:"
        )
        report.append("")
        for app, errors in resolution_failures.items():
            report.append(f"- **{app}**")
            for error in errors:
                report.append(f"  - `{error.replace('`', '')}`")
    else:
        report.append(f"All {len(apps)} monitored apps resolved successfully.")
    with open("apk-version-health.md", "w", encoding="utf-8") as handle:
        handle.write("\n".join(report) + "\n")


if __name__ == "__main__":
    sys.exit(main())
