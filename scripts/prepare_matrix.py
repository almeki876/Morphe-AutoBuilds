import json, os, sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

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

GBOARD_SOURCES = ('jason', 'adobo', 'morning-entree')
GBOARD_SOURCE_LABEL = 'jasonwu1994 + jkennethcarino + Entree3k'


def collapse_gboard_multi_source(items, all_config_items):
    """Represent Gboard's three patch sources as one chained build target.

    Any update to one of the three sources must rebuild the same integrated APK.
    Jason stays the matrix/source identity because its version-pinned bundle is the
    base compatibility authority; the patch step adds Adobo and Morning-Entree.
    """
    selected = [
        item for item in items
        if item.get('app_name') == 'gboard' and item.get('source') in GBOARD_SOURCES
    ]
    if not selected:
        return items

    jason = next(
        (
            item for item in all_config_items
            if item.get('app_name') == 'gboard' and item.get('source') == 'jason'
        ),
        None,
    )
    if jason is None:
        raise RuntimeError('Gboard multi-source build requires the jason config entry')

    integrated = dict(jason)
    integrated['patch_sources'] = list(GBOARD_SOURCES)
    integrated['source_label'] = GBOARD_SOURCE_LABEL
    return [
        item for item in items
        if not (
            item.get('app_name') == 'gboard'
            and item.get('source') in GBOARD_SOURCES
        )
    ] + [integrated]


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
updated_apps = {
    app.strip()
    for app in os.environ.get('UPDATED_APPS', '').split(',')
    if app.strip()
}

if build_all_sources:
    matrix = [i for i in all_items if is_enabled(i)]
elif updated_sources or updated_apps:
    matrix = [
        i for i in all_items
        if is_enabled(i)
        and (
            i['source'] in updated_sources
            or i['app_name'] in updated_apps
        )
    ]
else:
    matrix = []

matrix = collapse_gboard_multi_source(matrix, all_items)
for item in matrix:
    item.setdefault('source_label', source_labels.get(item['source'], item['source']))

if not matrix:
    print('WARNING: No sources or apps were updated - matrix is empty.', file=sys.stderr)

with open(os.environ['GITHUB_OUTPUT'], 'a') as f:
    f.write(f"matrix={json.dumps(matrix)}\n")
