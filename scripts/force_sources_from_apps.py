import json, os, sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

apps = json.loads(os.environ.get("APK_UPDATED_APPS", "[]"))
with open("my-patch-config.json") as f:
    patch_list = json.load(f)["patch_list"]

app_to_sources = {}
for item in patch_list:
    app_to_sources.setdefault(item["app_name"], set()).add(item["source"])

sources_to_force = set()
for app in apps:
    for src in app_to_sources.get(app, []):
        sources_to_force.add(src)

for src in sources_to_force:
    varname = src.replace("-", "_").upper()
    print(f"FORCE_{varname}=true")
