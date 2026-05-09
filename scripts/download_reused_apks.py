import json, os, re, sys, pathlib, subprocess
from urllib.parse import unquote
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

reuse_json = os.environ.get("REUSE_JSON", "{}").strip()

if not reuse_json or reuse_json in ("{}", ""):
    print("No APKs to reuse.")
    sys.exit(0)

def parse_reuse_json(s):
    """正規のJSONとして試みる。失敗した場合は {key:url,...} 形式をフォールバックとしてパース。"""
    # 試1: そのままパース
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        pass

    # 試2: GitHub Actionsがdouble-quoteを剥ぎ取った {key:url,...} 形式
    # 例: {src__name:https://...,src2__name2:https://...}
    s = s.strip()
    if s.startswith("{") and s.endswith("}"):
        inner = s[1:-1].strip()
        if not inner:
            return {}
        result = {}
        # URLを含むので単純なsplitは危険。URLは https:// で始まるので区切り位置を特定する
        # パターン: key:https://...[,key2:https://...]
        # 最初に全エントリを正規表現で抽出する
        pattern = re.compile(r'([^,{}:]+):(https?://[^,}]+)')
        for m in pattern.finditer(inner):
            key = m.group(1).strip()
            url = m.group(2).strip()
            result[key] = url
        if result:
            print(f"[WARN] REUSE_JSON was not valid JSON; parsed {len(result)} entries with fallback parser.", flush=True)
            return result

    print(f"[ERROR] Could not parse REUSE_JSON: {s[:200]}", file=sys.stderr)
    sys.exit(1)

data = parse_reuse_json(reuse_json)
if not data:
    print("No APKs to reuse (empty map).")
    sys.exit(0)

token = os.environ.get("GITHUB_TOKEN", "")
os.makedirs("./release-apks", exist_ok=True)

# 重複ファイル名を事前に検出して警告
fname_to_sources: dict[str, list[str]] = {}
for source, url in data.items():
    fname = unquote(url.split('/')[-1])
    fname_to_sources.setdefault(fname, []).append(source)
for fname, sources in fname_to_sources.items():
    if len(sources) > 1:
        print(f"[WARN] Duplicate filename detected ({fname}): {sources} — only the first will be kept.", file=sys.stderr)

seen_fnames: set[str] = set()
failed = []
for source, url in data.items():
    # URLエンコードされたファイル名(%2B等)をデコードして正規のファイル名に戻す。
    # デコードしないままだとgh release createが再エンコードして %252B になり HTTP 404 が発生する。
    fname = unquote(url.split('/')[-1])
    dest = f'./release-apks/{fname}'

    # 同名ファイルが既にダウンロード済みの場合はスキップ（重複回避）
    if fname in seen_fnames:
        print(f"[SKIP] {source}: {fname} already downloaded (duplicate key), skipping.", flush=True)
        continue
    seen_fnames.add(fname)
    print(f"Downloading reused APK for {source}: {url}", flush=True)
    result = subprocess.run(
        [
            'curl', '-sL', '--fail', '--retry', '3', '--retry-delay', '5',
            '-H', f'Authorization: token {token}',
            url, '-o', dest
        ],
        capture_output=True, text=True
    )
    if result.returncode != 0 or not pathlib.Path(dest).exists() or pathlib.Path(dest).stat().st_size == 0:
        print(f"[WARN] Failed to download {source}: {url} (exit={result.returncode})", file=sys.stderr)
        if result.stderr:
            print(f"       stderr: {result.stderr.strip()}", file=sys.stderr)
        # ダウンロード失敗のファイルは削除
        pathlib.Path(dest).unlink(missing_ok=True)
        failed.append(source)
    else:
        size = pathlib.Path(dest).stat().st_size
        print(f"  -> OK: {fname} ({size:,} bytes)", flush=True)

if failed:
    print(f"\n[WARN] {len(failed)} reused APK(s) could not be downloaded: {failed}", file=sys.stderr)
    print("Continuing without them (they will be missing from this release).", file=sys.stderr)
    # 致命的エラーとしない（リリース自体は続行する）

print(f"\nReuse download complete. Success: {len(data)-len(failed)}/{len(data)}")
