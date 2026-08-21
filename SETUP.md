# Morphe AutoBuilds 運用セットアップ

この文書は、Morphe AutoBuilds をフォークまたは独自リポジトリで運用する管理者向けです。公開 APK を使うだけなら設定は不要です。

## 前提

- GitHub Actions を利用できるリポジトリ
- Actions / Secrets / Variables / Releases を管理できる権限
- VirusTotal API キー
- Google Play を使う場合は、下記いずれかの認証方式

現在の通常ビルドには、削除済みの private `matchadaisuke/morphe-patches` / `yuzu` ソース用 PAT は不要です。CLI とパッチバンドルは [`sources/*.json`](sources/) に宣言された公開ソースだけを取得します。

Actions 内では Python 3.11 と Java 21 を準備します。

## 1. Actions を有効にする

GitHub の **Settings → Actions → General** で Actions を許可してください。ワークフローは用途ごとに最小限の `permissions` を宣言していますが、Organization policy や branch protection が優先されます。

必要な操作は主に次のとおりです。

- ソースコードの読み取り
- workflow dispatch
- GitHub Release / tag の作成
- base APK cache 用 release の更新
- `last-tags.json` の更新 push
- build failure Issue の作成・更新・クローズ

## 2. 必須 Secret

### `VIRUSTOTAL_API_KEY`

Repository secret として登録します。

```bash
gh secret set VIRUSTOTAL_API_KEY
```

未加工の元 APK は SHA-256 の既存結果を先に照会し、未知のハッシュだけを VirusTotal へアップロードします。検査を完了できない場合は、その APK を公開しません。

## 3. Google Play 認証

一般的な Play 配布アプリでは Google Play を最初に試します。認証方法は次のいずれかです。

### 方法 A: AAS token

Repository secrets:

- `GPLAY_EMAIL`
- `GPLAY_AAS_TOKEN`

### 方法 B: auth token

Repository secrets:

- `GPLAY_EMAIL`
- `GPLAY_AUTH_TOKEN`

### 方法 C: 匿名 token dispenser

コードには第三者 dispenser を固定していません。利用する endpoint を Repository variable へ設定します。

推奨:

- `GPLAY_DISPENSER_URLS` — 複数 endpoint。カンマ、セミコロン、改行で区切れます。

互換用:

- `GPLAY_DISPENSER_URL` — 単一 endpoint
- `AURORA_DISPENSER_URL` — 旧変数名

endpoint が API key を必要とする場合は Repository secret `GPLAYDL_API_KEY` を設定します。`gplaydl` は `X-Api-Key` ヘッダーとして送信します。

URL は `https://example.invalid` のようなベース URL でも `https://example.invalid/api/auth` でも構いません。複数 endpoint は指定順に試し、失敗レスポンス本文や token は CI ログへ出しません。

> [!NOTE]
> Google Play 認証を設定していなくても、アプリによっては他の取得元へフォールバックできます。ただし Google Play だけが要求版を提供している場合は取得に失敗します。

## 4. ビルド設定

設定の正本は次のファイルです。

| ファイル | 内容 |
| --- | --- |
| `my-patch-config.json` | 有効なアプリ/パッチソース、パッチオプション |
| `sources/*.json` | Morphe CLI とパッチバンドルの upstream release |
| `apps/**.json` | package ID と元 APK 取得設定 |
| `arch-config.json` | アーキテクチャの個別指定 |
| `last-tags.json` | 最後に正常公開できた upstream/APK 状態 |

新しいパッチソースを追加する場合、`sources/<source>.json` と `my-patch-config.json` を追加すれば、定期 updater と tool downloader は自動的にそのソースを走査します。`build.yml` や `check-upstream.yml` に `SOURCE_NAME_updated` のような固定 input を追加する必要はありません。

Gboard は `jason`、`adobo`、`morning-entree` の 3 ソースを 1 つの統合マトリクス項目へまとめます。

## 5. 手動ビルド

### 全有効エントリ

Actions から **Trigger Test Build All (Release)** (`test-build.yml`) を実行します。内部で `build.yml` を `build_all_sources=true` で dispatch します。

直接実行する場合:

```bash
gh workflow run build.yml -f build_all_sources=true
```

### 特定ソース

```bash
gh workflow run build.yml \
  -f updated_sources='adobo,morning-entree'
```

`anddea` は内部 source id `revanced-anddea` と同じものとして扱います。

### 特定アプリ

```bash
gh workflow run build.yml \
  -f updated_apps='gboard,amazon-shopping'
```

## 6. 定期更新確認

`check-upstream.yml` は毎日 09:00 UTC（JST 18:00 頃）に実行されます。

- `scripts/check_upstream_sources.py` が `sources/*.json` の全パッチ repository を確認
- `scripts/detect_version_pinned.py` が version-pinned 対象を確認
- `scripts/check_apk_versions.py` が監視 APK の更新を確認
- 変更があれば `updated_sources` / `updated_apps` を `build.yml` へ渡す

固定された手動ソース一覧は使用しません。

`last-tags.json` は更新を検出した時点では進めません。選択されたビルド、VirusTotal、release が成功した後に `scripts/save_successful_state.py` が現在の宣言済みソースと APK 状態を保存します。

## 7. 元 APK の取得と検証

一般経路では Google Play を優先し、失敗時に JustAPK、apkeep、APKMirror、APKPure、Uptodown、Softonic、Aptoide、APKCombo などの利用可能な取得経路へフォールバックします。アプリ固有の公式 GitHub Release や専用 provider がある場合は、その設定が優先されます。

候補 APK は採用前に package id、version name/version code、ABI を検証します。要求版と違う APK や HTML/error page は破棄して次へ進みます。`universal` は特定 ABI 一致を要求しません。

取得元を個別確認する場合:

```bash
python scripts/probe_apk_sources.py \
  --app nova \
  --version 8.8.6 \
  --code 88600 \
  --arch arm64-v8a
```

## 8. CI / validation

Pull Request と main への push では `configuration-check.yml` が次を実行します。

1. `scripts/validate_repository.py`
2. `python3 -m compileall -q src scripts`
3. provider configuration validation
4. `python3 -m unittest discover tests`

実 APK を伴う確認が必要な変更では `pr-targeted-build-verification.yml` または手動 `build.yml` を使います。

## 9. リリース

正常な release には成功 APK と release notes が含まれます。元 APK の VirusTotal report は Actions artifact にも保存されます。

一部の対象だけが失敗した場合は Partial release になることがあります。全対象が成功していない場合、`last-tags.json` は進めません。

## トラブルシューティング

### `anonymous Google Play authentication requires ...`

`GPLAY_EMAIL` + token がなく、dispenser URL も設定されていません。`GPLAY_DISPENSER_URLS` または直接認証用 secret を設定してください。

### `all configured Google Play token dispensers failed`

- endpoint が到達可能か
- `/api/auth` を受け付ける互換 API か
- API key が必要なら `GPLAYDL_API_KEY` が正しいか
- 複数 endpoint を `GPLAY_DISPENSER_URLS` に設定できないか

を確認します。CI のエラーには認証レスポンス本文を出さないため、必要なら dispenser 側ログを確認してください。

### 元 APK の version mismatch

フォールバック provider が別版を返しています。その候補は自動的に拒否されます。要求 version/version code が provider に存在するか、`apps/` の設定やパッチ互換版を確認してください。

### ビルドマトリクスが空

手動 `build.yml` では `build_all_sources=true`、`updated_sources`、`updated_apps` のいずれかを指定してください。

### パッチが適用されたように見えるが失敗扱い

現行コードは要求パッチと CLI が報告した適用パッチを照合します。選択した feature patch が option 不足などで落ちた場合は、APK が出力されても成功にしません。Build result artifact と Issue の patch details を確認してください。

### VirusTotal で release が止まる

`VIRUSTOTAL_API_KEY`、利用上限、解析 status を確認してください。未検査の APK を公開しない設計です。

## セキュリティ

Secret や token をコミット、Issue、Actions summary へ貼らないでください。Google Play token dispenser のレスポンス本文も認証情報を含む可能性があります。Actions には必要な secret/variable だけを設定し、使わなくなった credential は削除してください。
