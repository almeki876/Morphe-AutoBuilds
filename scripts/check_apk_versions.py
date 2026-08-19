import json, os, sys, logging, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
logging.basicConfig(level=logging.WARNING)

last = {}
if os.path.exists("last-tags.json"):
    try:
        with open("last-tags.json", encoding="utf-8") as f:
            content = f.read().strip()
        if content:
            last = json.loads(content)
    except (json.JSONDecodeError, ValueError) as e:
        logging.warning("last-tags.json is empty or corrupt, treating as {}: %s", e)

with open("my-patch-config.json", encoding="utf-8") as f:
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

from src import providers, utils
from src.versioning import canonical_version

any_apk_updated = False
apk_updated_apps = []
resolution_failures = {}

github_output = open(os.environ["GITHUB_OUTPUT"], "a")

for app in apps:
    key = f"apk_{app}"
    prev = last.get(key, "")

    cur = None
    provider_errors = []
    for platform in providers.download_priority(app):
        try:
            config = providers.load_config(app, platform)
            if config is None:
                provider_errors.append(f"{platform}: no configuration")
                continue
            ver = providers.MODULES[platform].get_latest_version(app, config)
            if ver:
                cur = canonical_version(ver)
                break
            provider_errors.append(f"{platform}: returned no version")
        except Exception as error:
            provider_errors.append(
                f"{platform}: {type(error).__name__}: "
                f"{utils.safe_text_for_log(error, 300)}"
            )
            continue

    if cur is None:
        print(f"WARNING: {app}: could not resolve APK version, skipping")
        resolution_failures[app] = provider_errors
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
github_output.write(f"updated_apps={','.join(apk_updated_apps)}\n")
github_output.write(
    f"apk_version_health_ok={str(not resolution_failures).lower()}\n"
)
github_output.write(
    f"apk_version_failed_apps={json.dumps(sorted(resolution_failures))}\n"
)
github_output.close()

report = ["## APK version discovery", ""]
if resolution_failures:
    report.append(
        "The following apps had no working version provider. Existing saved "
        "versions were preserved:"
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

if resolution_failures:
    raise SystemExit(1)
