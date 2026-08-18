import json, os, sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

upd = {
    'morphe':          os.environ.get('UPD_MORPHE',  'false'),
    'revanced-anddea': os.environ.get('UPD_ANDDEA',  'false'),
    'hoo':             os.environ.get('UPD_HOO',     'false'),
    'rookie':          os.environ.get('UPD_ROOKIE',  'false'),
    'tosox':           os.environ.get('UPD_TOSOX',   'false'),
    'yuzu':            os.environ.get('UPD_YUZU',    'false'),
    'dropped':         os.environ.get('UPD_DROPPED', 'false'),
}

# force_build: APK更新による再ビルド強制
force = {
    'morphe':          os.environ.get('FORCE_MORPHE',   'false'),
    'revanced-anddea': os.environ.get('FORCE_ANDDEA',   'false'),
    'hoo':             os.environ.get('FORCE_HOO',      'false'),
    'rookie':          os.environ.get('FORCE_ROOKIE',   'false'),
    'tosox':           os.environ.get('FORCE_TOSOX',    'false'),
    'yuzu':            os.environ.get('FORCE_YUZU',     'false'),
    'dropped':         os.environ.get('FORCE_DROPPED',  'false'),
}

apk_updated_raw = os.environ.get('APK_UPDATED_APPS', '[]')
try:
    apk_updated_apps = set(json.loads(apk_updated_raw))
except Exception:
    apk_updated_apps = set()

with open("./my-patch-config.json", encoding="utf-8") as config_file:
    all_items = json.load(config_file)["patch_list"]

def should_build_item(item):
    source = item['source']
    app_name = item['app_name']

    # 1. パッチソース自体が更新された場合 -> そのソースの対象全アプリをビルド
    if upd.get(source) == 'true':
        return True

    # 2. APK本体の更新により再ビルドする場合 -> 実際に更新が検出されたアプリのみビルド
    if force.get(source) == 'true':
        if apk_updated_apps:
            return app_name in apk_updated_apps
        return True

    return False

all_true = all(v == 'true' for v in upd.values())
matrix = all_items if all_true else [i for i in all_items if should_build_item(i)]

if not matrix:
    print('WARNING: No sources or apps were updated - matrix is empty.', file=sys.stderr)

with open(os.environ['GITHUB_OUTPUT'], 'a') as f:
    f.write(f"matrix={json.dumps(matrix)}\n")
