import json, os, sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

upd = {
    'morphe':          os.environ.get('UPD_MORPHE',         'false'),
    'revanced-anddea': os.environ.get('UPD_ANDDEA',         'false'),
    'rushiranpise':    os.environ.get('UPD_RUSHIRANPISE',   'false'),
    'hoomans':         os.environ.get('UPD_HOOMANS',        'false'),
    'durgesh0505':     os.environ.get('UPD_DURGESH0505',     'false'),
    'icysymmetra':     os.environ.get('UPD_ICYSYMMETRA',      'false'),
    'fluffy':          os.environ.get('UPD_FLUFFY',           'false'),
    'quantro':         os.environ.get('UPD_QUANTRO',          'false'),
    'shaun-the-sheep-patches': os.environ.get('UPD_SHAUN_THE_SHEEP_PATCHES', 'false'),
    'rookie':          os.environ.get('UPD_ROOKIE',         'false'),
    'tosox':           os.environ.get('UPD_TOSOX',          'false'),
    'yuzu':            os.environ.get('UPD_YUZU',           'false'),
    'dropped':         os.environ.get('UPD_DROPPED',        'false'),
    'lain':            os.environ.get('UPD_LAIN',           'false'),
    'jason':           os.environ.get('UPD_JASON',          'false'),
    'adobo':           os.environ.get('UPD_ADOBO',          'false'),
    'morning-entree':  os.environ.get('UPD_MORNING_ENTREE', 'false'),
    'ajstrick81':     os.environ.get('UPD_AJSTRICK81',     'false'),
    'andrewliang25':  os.environ.get('UPD_ANDREWLIANG25',  'false'),
    'hoo-dles':        os.environ.get('UPD_HOO_DLES',       'false'),
    'bholey':          os.environ.get('UPD_BHOLEY',         'false'),
    'paresh':          os.environ.get('UPD_PARESH',         'false'),
    'dh6k':            os.environ.get('UPD_DH6K',           'false'),
}

# force_build: APK更新による再ビルド強制
force = {
    'morphe':          os.environ.get('FORCE_MORPHE',         'false'),
    'revanced-anddea': os.environ.get('FORCE_ANDDEA',         'false'),
    'rushiranpise':    os.environ.get('FORCE_RUSHIRANPISE',  'false'),
    'hoomans':         os.environ.get('FORCE_HOOMANS',        'false'),
    'durgesh0505':     os.environ.get('FORCE_DURGESH0505',     'false'),
    'icysymmetra':     os.environ.get('FORCE_ICYSYMMETRA',      'false'),
    'fluffy':          os.environ.get('FORCE_FLUFFY',           'false'),
    'quantro':         os.environ.get('FORCE_QUANTRO',          'false'),
    'shaun-the-sheep-patches': os.environ.get('FORCE_SHAUN_THE_SHEEP_PATCHES', 'false'),
    'rookie':          os.environ.get('FORCE_ROOKIE',         'false'),
    'tosox':           os.environ.get('FORCE_TOSOX',          'false'),
    'yuzu':            os.environ.get('FORCE_YUZU',           'false'),
    'dropped':         os.environ.get('FORCE_DROPPED',        'false'),
    'lain':            os.environ.get('FORCE_LAIN',           'false'),
    'jason':           os.environ.get('FORCE_JASON',          'false'),
    'adobo':           os.environ.get('FORCE_ADOBO',          'false'),
    'morning-entree':  os.environ.get('FORCE_MORNING_ENTREE', 'false'),
    'ajstrick81':     os.environ.get('FORCE_AJSTRICK81',     'false'),
    'andrewliang25':  os.environ.get('FORCE_ANDREWLIANG25',  'false'),
    'hoo-dles':        os.environ.get('FORCE_HOO_DLES',       'false'),
    'bholey':          os.environ.get('FORCE_BHOLEY',         'false'),
    'paresh':          os.environ.get('FORCE_PARESH',         'false'),
    'dh6k':            os.environ.get('FORCE_DH6K',           'false'),
}

source_labels = {
    'morphe': 'Morphe',
    'revanced-anddea': 'Anddea',
    'rushiranpise': 'rushiranpise',
    'hoomans': 'arandomhooman',
    'rookie': 'RookieEnough',
    'durgesh0505': 'durgesh0505',
    'icysymmetra': 'icysymmetra',
    'ajstrick81': 'ajstrick81',
    'andrewliang25': 'andrewliang25',
    'hoo-dles': 'hoo-dles',
    'fluffy': 'rabilrbl',
    'quantro': 'Quantro100',
    'lain': 'kiraio-moe',
    'jason': 'jasonwu1994',
    'adobo': 'jkennethcarino',
    'morning-entree': 'Entree3k',
    'bholey': 'BholeyKaBhakt',
    'paresh': 'Paresh-Maheshwari',
    'dh6k': 'dh6k',
    'shaun-the-sheep-patches': 'shaun-the-sheep-patches',
}

apk_updated_raw = os.environ.get('APK_UPDATED_APPS', '[]')
try:
    apk_updated_apps = set(json.loads(apk_updated_raw))
except Exception:
    apk_updated_apps = set()

with open("./my-patch-config.json", encoding="utf-8") as config_file:
    all_items = json.load(config_file)["patch_list"]


def is_enabled(item):
    """Return whether a configured app/source pair belongs in the matrix."""
    return item.get("enabled", True) is not False and item.get("skip_build", False) is not True

build_all_sources = os.environ.get('BUILD_ALL_SOURCES', 'false') == 'true'
updated_sources = {
    source.strip()
    for source in os.environ.get('UPDATED_SOURCES', '').split(',')
    if source.strip()
}
if 'anddea' in updated_sources:
    updated_sources.add('revanced-anddea')
updated_apps = {
    app.strip()
    for app in os.environ.get('UPDATED_APPS', '').split(',')
    if app.strip()
}

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

all_true = build_all_sources or all(v == 'true' for v in upd.values())
if build_all_sources:
    matrix = [i for i in all_items if is_enabled(i)]
elif updated_sources:
    matrix = [i for i in all_items if is_enabled(i) and i['source'] in updated_sources]
elif updated_apps:
    matrix = [i for i in all_items if is_enabled(i) and i['app_name'] in updated_apps]
else:
    matrix = [i for i in all_items if is_enabled(i)] if all_true else [
        i for i in all_items if is_enabled(i) and should_build_item(i)
    ]
for item in matrix:
    item['source_label'] = source_labels.get(item['source'], item['source'])

if not matrix:
    print('WARNING: No sources or apps were updated - matrix is empty.', file=sys.stderr)

with open(os.environ['GITHUB_OUTPUT'], 'a') as f:
    f.write(f"matrix={json.dumps(matrix)}\n")
