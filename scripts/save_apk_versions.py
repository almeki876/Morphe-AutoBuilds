import json, os, sys, logging, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
logging.basicConfig(level=logging.WARNING)

current = {}
if os.path.exists("last-tags.json"):
    try:
        with open("last-tags.json", encoding="utf-8") as f:
            content = f.read().strip()
        if content:
            current = json.loads(content)
    except (json.JSONDecodeError, ValueError) as e:
        logging.warning("last-tags.json is empty or corrupt, starting fresh: %s", e)

with open("my-patch-config.json", encoding="utf-8") as f:
    patch_list = json.load(f)["patch_list"]

seen = set()
apps = []
for item in patch_list:
    if item["app_name"] not in seen:
        seen.add(item["app_name"])
        apps.append(item["app_name"])

from src import google_play_metadata, providers, utils
from src.versioning import canonical_version

for app in apps:
    key = f"apk_{app}"
    provider_errors = []
    resolved = False
    package = providers.configured_package(app)

    # Keep the persisted identity identical to check_apk_versions.py.  Using a
    # mirror here after detecting Google Play caused the same app to look
    # updated again on every scheduled run when the providers used different
    # version-name formats.
    if package:
        try:
            play_identity = google_play_metadata.current_release_identity(package)
            if play_identity is not None:
                current[key] = canonical_version(play_identity.name)
                resolved = True
        except Exception as error:
            provider_errors.append(
                f"google-play: {type(error).__name__}: "
                f"{utils.safe_text_for_log(error, 300)}"
            )

    for platform in providers.download_priority(app):
        if resolved:
            break
        try:
            config = providers.load_config(app, platform)
            if config is None:
                continue
            ver = providers.MODULES[platform].get_latest_version(app, config)
            if ver:
                current[key] = canonical_version(ver)
                resolved = True
                break
            provider_errors.append(f"{platform}: returned no version")
        except Exception as error:
            provider_errors.append(
                f"{platform}: {type(error).__name__}: "
                f"{utils.safe_text_for_log(error, 300)}"
            )
            continue
    if not resolved:
        logging.warning(
            "%s: version resolution failed; preserving previous value %r. %s",
            app,
            current.get(key),
            "; ".join(provider_errors),
        )

with open("last-tags.json", "w", encoding="utf-8") as f:
    json.dump(current, f, indent=2)
print("APK versions saved to last-tags.json")
