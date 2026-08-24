# Morphe AutoBuilds 開発・運用ガイド

この文書は、Morphe AutoBuilds をフォーク・改修・運用する開発者／メンテナ向けです。APKを利用するだけの場合は [README](./README.md) を参照してください。

## 全体構成

このリポジトリは次の責務に分かれています。

- `my-patch-config.json` — ビルドするアプリとパッチソース
- `arch-config.json` — 出力アーキテクチャ
- `app-metadata/` — provider非依存のpackage ID・取得ポリシー
- `apps/<provider>/` — APK取得元ごとの設定
- `sources/` — パッチCLI／bundleの取得設定
- `src/` — provider、Google Play、version解決、patch/buildなどの共通実装
- `scripts/` — Actionsから呼ぶ運用処理
- `.github/workflows/` — 実行タイミングとジョブ依存関係
- `tests/` — 回帰テスト

原則として、複雑な判定やネットワーク処理はYAMLへ直接増やさず `src/` / `scripts/` に実装し、workflowは「いつ・何を呼ぶか」に寄せます。

## GitHub Actions の見方

Actions画面では用途が分かるよう、workflow名を次の分類で管理します。

| Workflow | 用途 |
| --- | --- |
| `Build and Release APKs` | 通常のビルド・検査・Release本体 |
| `Build All Apps` | 全対象を手動で強制ビルドする入口 |
| `Check Upstream for Updates` | 定期的にパッチ／APK更新を確認し必要時だけbuildを起動 |
| `Configuration Check` | push / PR時の設定・構文・unit test |
| `Repository and APK Provider Health` | providerやbuild toolの定期ヘルスチェック |
| `Register Google Play Account` | gplaydl用Googleアカウント登録 |
| `Diagnose Google Play Purchase` | Google Play取得の手動診断 |
| `Verify Japanese APK Download Fallback` | Tailscale日本出口の手動診断 |
| `Publish VirusTotal Cache` | 成功したVT hash結果の永続化 |
| `Update Direct Download Links` | Release後の直リンク一覧更新 |

通常運用で手動実行することが多いのは `Build All Apps`、`Register Google Play Account`、必要時の2つの診断workflowです。

## 必要なSecrets / Variables

Repository Secrets:

| 名前 | 用途 |
| --- | --- |
| `PAT` | 非公開patch sourceの読み取り、Google Play登録時のsecret更新 |
| `VIRUSTOTAL_API_KEY` | 未加工base APKのVirusTotal検査 |
| `GPLAYDL_API_KEY` | gplaydl Google Play取得 |
| `GPLAY_EMAIL` | 登録済みGoogle Playアカウント |
| `GPLAY_AAS_TOKEN` | gplaydl Authenticatorで取得したAAS token |
| `TS_OAUTH_CLIENT_ID` | Tailscale Actions接続 |
| `TS_OAUTH_SECRET` | Tailscale Actions接続 |

Repository Variables:

| 名前 | 用途 |
| --- | --- |
| `TS_EXIT_NODE` | 日本にあるTailscale exit node |
| `GPLAYDL_PREFERRED_PROFILE` | 優先device profile（未指定なら自動） |

Secret値はログ・Issue・コミットへ書かないでください。

## Google Play の取得設計

Google Playからexact versionを取得する前に、パッチCLIが返した互換 `versionName` を基準に、provider metadataからAndroid `versionCode` を解決・検証します。

`src/providers.py` の identity resolver は、パッチ互換性を変更せず、同じversionNameに対応するversionCodeだけを補完します。versionCodeを安全に特定できない場合は、近い別バージョンを推測して取得しません。

Google Play取得は `src/aurora_play.py` が担当します。versionCodeがある場合、gplaydlへ `-v <versionCode>` を渡して完全一致版を要求します。

通常の優先順は速度と独立実装の安全網を両立するため、概ね次の通りです。

1. 軽量な gplaydl
2. playfetch
3. apkeep
4. 必要な場合のみfresh device/profileを使うgplaydl

各外部コマンドには個別timeoutとGoogle Play全体の時間予算があり、失敗した経路へ無期限に待機しません。

## APKダウンロードのフォールバック

実Actionsジョブでは、可用性を優先して次の順番で救済します。

1. 通常runner IPでGoogle Play
2. Google Playが失敗したらTailscale日本出口へ接続し、JP egressを確認してGoogle Play再試行
3. JP Google Playでも取得できなければ、通常IPで設定済みprovider／mirrorを試行
4. Cloudflare/CDN等で通常runner IPも拒否された場合のみ、最後にTailscale JP経由でprovider chainを1回だけ再試行

最後のprovider再試行ではGoogle Playをskipし、再帰ループやGoogle Playへの無駄な再接続を防ぎます。

HTTP providerは一時 `.part` へstreaming downloadし、Content-Length、APK/ZIP妥当性、package/version identityを確認した後だけ確定ファイルとして採用します。途中切断やHTML誤取得は成功扱いしません。

## 日本限定アプリ

ゆうちょ通帳・ゆうちょ認証のようにGoogle Play上で地域条件があるアプリでは、日本のTailscale exit nodeを重要な取得経路として維持します。

運用するGoogleアカウント側でも、対象アプリを日本のGoogle Play上で取得可能な状態にしてください。Tailscale接続後は `scripts/verify_japan_egress.py` で外向きIPが日本であることを確認してから認証情報を使います。

## VirusTotal

公開前に未加工base APKをSHA-256で確認します。

1. 永続cacheで同一SHAを確認
2. VirusTotal `GET /files/{sha256}` で既存結果を検索
3. VTに存在しないSHAだけupload
4. 新規解析を開始した後、pollをまとめて実行
5. clean判定をcacheへ即保存

Google Play split APKをまとめる `.apks` は決定論的に生成し、同じAPK群ならrunnerやmtimeが違っても同じSHAになるようにしています。

Actions cacheに加え、成功したVT結果はRelease assetにも永続化し、Actions cache消失時にも再利用します。VirusTotalを確認できない場合はfail-closedでReleaseを止めます。

## Google Play アカウント登録

1. `GPLAYDL_API_KEY` をSecretへ登録
2. Actionsから `Register Google Play Account` を実行
3. `expected_email` に利用するGoogleアカウントを指定
4. Job Summaryに出る一時Authenticator URLを公式gplaydl Authenticatorへ設定
5. 指定アカウントを追加
6. workflowが `GPLAY_EMAIL` / `GPLAY_AAS_TOKEN` を保存したことを確認

登録終了後、一時URLは利用し続けないでください。

## 初回セットアップ

1. GitHub Actionsを有効化
2. 上記Secrets / Variablesを設定
3. `python3 scripts/validate_repository.py` が成功する状態にする
4. `Configuration Check` を確認
5. `Verify Japanese APK Download Fallback` でTailscale JP経路を確認
6. 必要ならGoogle Playアカウントを登録
7. `Build All Apps` を実行して全体動作を確認

## ローカル検証

最低限、PR前に次を実行します。

```bash
pip install -r requirements.txt
python3 scripts/validate_repository.py
python3 -m compileall -q src scripts
python3 -m unittest discover tests
```

GitHub Actionsの `Configuration Check` も同じ系統の検証を行います。

## 設定変更の原則

- package IDはproviderごとに重複して定義せず、矛盾させない
- patch CLIが返した互換versionNameを勝手に別versionへ置換しない
- `version_code` を設定ファイルへ固定保存しない
- download失敗を「最新版で代用」して成功扱いしない
- providerのHTMLや壊れたAPKをcacheへ入れない
- Google Play / Tailscale / VT secretをログへ出さない
- workflowへ長い判定ロジックを追加する前にscript/module化を検討する

## 自動更新と状態保存

`Check Upstream for Updates` がpatch sourceとAPK更新を確認し、変更対象だけ `build.yml` をdispatchします。

成功したビルド状態は `last-tags.json` に保存します。mainへ別workflowが同時pushしてもnon-fast-forwardで失敗しにくいよう、state保存処理は最新mainへこのrunの変更だけを再適用してpushします。force pushは使用しません。

## Release

Releaseは次を満たした場合だけ作成します。

- build対象から少なくとも1 APKが完成
- VirusTotal base APK scanが成功
- package/version identity検証を通過

一部アプリが失敗した場合は、正常に完成したAPKだけが部分Releaseになることがあります。失敗情報はper-app status artifactとIssue reporterへ渡されます。

## メンテナンス用workflow

### Diagnose Google Play Purchase

Google Playで特定package/versionCodeが取得できない場合の調査用です。通常ビルドの代わりには使いません。

### Verify Japanese APK Download Fallback

Tailscale接続、exit node選択、日本IP確認だけを独立して確認します。Google Play障害とTailscale障害を切り分けるために使用します。

### Repository and APK Provider Health

build toolとAPK providerを定期probeします。通常ビルドの一時的な障害とは分けて、provider全体の劣化を検出するためのworkflowです。

## ファイル整理の判断基準

ファイルを削除するときは、名前だけで「古そう」と判断せず、次を確認します。

1. workflowから参照されていないか
2. Python importがないか
3. testsから参照されていないか
4. README / SETUP / generated docsから参照されていないか
5. 手動診断用途ではないか

参照がなくても、migrationやdiagnosticのために意図的に残している場合は用途をコメントまたはSETUPへ記録します。

## 利用者向け情報

APKのダウンロード方法、アーキテクチャ、安全性、問題報告など、利用者が必要な情報は [README.md](./README.md) に限定します。内部実装をREADMEへ増やさず、このSETUPへ追記してください。
