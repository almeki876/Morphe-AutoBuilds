import json, os, sys, logging, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
logging.basicConfig(level=logging.WARNING)

last = {}
if os.path.exists("last-tags.json"):
    try:
        with open("last-tags.json") as f:
            content = f.read().strip()
        if content:
            last = json.loads(content)
    except (json.JSONDecodeError, ValueError) as e:
        logging.warning("last-tags.json is empty or corrupt, treating as {}: %s", e)

with open("my-patch-config.json") as f:
    patch_list = json.load(f)["patch_list"]

# detect_version_pinned.py が検出した「推奨バージョン固定アプリ」はスキップ
# （パッチ側がバージョンを指定するため、APK本体の更新は再ビルドに不要）
version_pinned_raw = os.environ.get("VERSION_PINNED_APPS", "[]")
try:
    VERSION_PINNED = set(json.loads(version_pinned_raw))
except Exception:
    VERSION_PINNED = set()

if VERSION_PINNED:
    logging.warning("APK監視スキップ対象（推奨バージョン固定）: %s", sorted(VERSION_PINNED))

seen = set()
apps = []
for item in patch_list:
    if item["app_name"] not in seen and item["app_name"] not in VERSION_PINNED:
        seen.add(item["app_name"])
        apps.append(item["app_name"])

from src import apkmirror, apkpure, uptodown, aptoide, github as github_mod

platform_order = ["apkmirror", "apkpure", "uptodown", "aptoide", "github"]

any_apk_updated = False
apk_updated_apps = []

github_output = open(os.environ["GITHUB_OUTPUT"], "a")

for app in apps:
    key = f"apk_{app}"
    prev = last.get(key, "")

    cur = None
    for platform in platform_order:
        config_path = pathlib.Path("apps") / platform / f"{app}.json"
        if not config_path.exists():
            continue
        try:
            with open(config_path) as f:
                config = json.load(f)
            mod_map = {
                "github": github_mod,
                "apkmirror": apkmirror,
                "apkpure": apkpure,
                "uptodown": uptodown,
                "aptoide": aptoide,
            }
            ver = mod_map[platform].get_latest_version(app, config)
            if ver:
                cur = str(ver)
                break
        except Exception:
            continue

    if cur is None:
        print(f"WARNING: {app}: could not resolve APK version, skipping")
        github_output.write(f"apkver_{app}=false\n")
        continue

    if cur != prev:
        print(f"UPDATED: {app} APK updated: {prev!r} -> {cur!r}")
        any_apk_updated = True
        apk_updated_apps.append(app)
        github_output.write(f"apkver_{app}=true\n")
    else:
        print(f"UNCHANGED: {app} APK unchanged: {cur}")
        github_output.write(f"apkver_{app}=false\n")

github_output.write(f"any_apk_updated={str(any_apk_updated).lower()}\n")
github_output.write(f"apk_updated_apps={json.dumps(apk_updated_apps)}\n")
github_output.close()
