import json, os, sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

upd = {
    'morphe':          os.environ.get('UPD_MORPHE',  'false'),
    'revanced-anddea': os.environ.get('UPD_ANDDEA',  'false'),
    'piko':            os.environ.get('UPD_PIKO',    'false'),
    'hoo':             os.environ.get('UPD_HOO',     'false'),
    'rookie':          os.environ.get('UPD_ROOKIE',  'false'),
    'tosox':           os.environ.get('UPD_TOSOX',   'false'),
    'yuzu':            os.environ.get('UPD_YUZU',    'false'),
    'dropped':         os.environ.get('UPD_DROPPED', 'false'),
}

# force_build: APK更新による再ビルド強制（NEWバッジには使わない）
force = {
    'morphe':          os.environ.get('FORCE_MORPHE',   'false'),
    'revanced-anddea': os.environ.get('FORCE_ANDDEA',   'false'),
    'piko':            os.environ.get('FORCE_PIKO',     'false'),
    'hoo':             os.environ.get('FORCE_HOO',      'false'),
    'rookie':          os.environ.get('FORCE_ROOKIE',   'false'),
    'tosox':           os.environ.get('FORCE_TOSOX',    'false'),
    'yuzu':            os.environ.get('FORCE_YUZU',     'false'),
    'dropped':         os.environ.get('FORCE_DROPPED',  'false'),
}

all_items = json.load(open('./my-patch-config.json'))['patch_list']

def should_build(source):
    return upd.get(source) == 'true' or force.get(source) == 'true'

all_true = all(v == 'true' for v in upd.values())
matrix = all_items if all_true else [i for i in all_items if should_build(i['source'])]

if not matrix:
    print('WARNING: No sources were updated - matrix is empty.', file=sys.stderr)

with open(os.environ['GITHUB_OUTPUT'], 'a') as f:
    f.write(f"matrix={json.dumps(matrix)}\n")
