# Morphe AutoBuilds

[日本語] | [English](README.en.md)

[![Upstream Check](https://img.shields.io/github/actions/workflow/status/almeki876/Morphe-AutoBuilds/check-upstream.yml?label=upstream%20check)](https://github.com/almeki876/Morphe-AutoBuilds/actions/workflows/check-upstream.yml)
[![Build Status](https://img.shields.io/github/actions/workflow/status/almeki876/Morphe-AutoBuilds/build.yml?label=build)](https://github.com/almeki876/Morphe-AutoBuilds/actions/workflows/build.yml)
[![Latest Release](https://img.shields.io/github/v/release/almeki876/Morphe-AutoBuilds?label=latest%20release)](https://github.com/almeki876/Morphe-AutoBuilds/releases/latest)

Morphe AutoBuilds は、Android アプリの元 APK を取得し、複数のコミュニティ製 Morphe/ReVanced 系パッチを適用して署名済み APK を自動生成・公開する GitHub Actions ベースのビルドリポジトリです。

> [!IMPORTANT]
> このリポジトリと生成 APK は、各アプリの開発元、Google、Morphe、ReVanced、各パッチ作者の公式配布物ではありません。利用前に元 APK の取得元、VirusTotal 結果、適用パッチをリリースノートで確認してください。

## ダウンロード

- [最新リリース](https://github.com/almeki876/Morphe-AutoBuilds/releases/latest)
- [Obtainium / ObtainX 用リンク](Morphe-AutoBuilds-Obtainium.md)

APK ファイル名にはアプリ名、パッチソース、元 APK バージョン、対象アーキテクチャなどが含まれます。通常は `arm64-v8a` を優先し、必要に応じて `universal` を使用します。

## 現在のビルド対象

ビルド対象の正本は [`my-patch-config.json`](my-patch-config.json) です。パッチソースのリポジトリと CLI/パッチ資産は [`sources/`](sources/) に、元 APK の取得設定は [`apps/`](apps/) にあります。

README に固定の対象一覧を複製しないことで、設定追加時にドキュメントだけが古くなる状態を避けています。実際の組み合わせは `my-patch-config.json` と各リリースの Build results を確認してください。

### Gboard

Gboard は例外的に 1 APK へ 3 ソースを連結して適用します。

1. `jason` (`jasonwu1994/Gboard-patches`) — 互換バージョンの基準
2. `adobo` (`jkennethcarino/adobo`)
3. `morning-entree` (`Entree3k/Morning-Entree-Patches`)

3 つのうちどれかが更新されると統合 Gboard ビルドが選択されます。要求したパッチが CLI によって黙って落とされた場合も成功扱いにせず、ビルド結果へ記録します。

## 自動ビルドの流れ

`check-upstream.yml` は毎日 09:00 UTC（JST 18:00 頃）に実行され、`sources/*.json` に宣言された全パッチソースと監視対象 APK の更新を確認します。更新されたソース ID は `updated_sources`、更新されたアプリは `updated_apps` として `build.yml` へ渡されます。

`build.yml` は固定のソース別入力を持たず、現在の設定から対象マトリクスを生成します。

1. `sources/*.json` から必要な CLI とパッチバンドルを取得
2. 更新されたソース/アプリからビルドマトリクスを生成
3. 互換バージョンの元 APK を取得
4. package ID・version name/version code・ABI を検証
5. 未加工の元 APK を VirusTotal 検査用に保存
6. Morphe CLI でパッチを適用し、結果と実際の適用パッチを検証
7. APK を署名
8. VirusTotal が通過した元 APK に対応する完成 APK を GitHub Releases へ公開
9. 全対象が成功した場合のみ `last-tags.json` の成功状態を更新

一部の対象だけが失敗した場合は成功した APK だけを Partial release として公開でき、アプリ単位の失敗は Issue と Build result artifact に記録されます。

## 元 APK の取得

一般的な APK は **Google Play を最優先**に試し、その後にサイト/API ベースのフォールバックを使います。現在の実装には Google Play、JustAPK、apkeep、APKMirror、APKPure、Uptodown、Softonic、Aptoide、APKCombo などの経路があり、アプリ固有の公式 GitHub Release や専用取得元が設定されている場合はそのルールが優先されます。

ダウンロードした候補は使用前に [`src/apk_validation.py`](src/apk_validation.py) で検証します。期待 package/version と一致しない候補は破棄して次の取得元へ進み、`universal` APK では特定 ABI の存在を必須にしません。

### Google Play 認証

リポジトリローカルの `gplaydl` は次の順で認証を試します。

- `GPLAY_EMAIL` + `GPLAY_AAS_TOKEN`
- `GPLAY_EMAIL` + `GPLAY_AUTH_TOKEN`
- 設定された匿名 token dispenser

匿名 dispenser はコードに固定されません。Actions では次の Repository variables / secrets を利用できます。

| 名前 | 種別 | 用途 |
| --- | --- | --- |
| `GPLAY_DISPENSER_URLS` | Variable | カンマ・セミコロン・改行区切りの複数 endpoint（推奨） |
| `GPLAY_DISPENSER_URL` | Variable | 単一 endpoint |
| `AURORA_DISPENSER_URL` | Variable | 旧設定名との互換用 |
| `GPLAYDL_API_KEY` | Secret | dispenser が `X-Api-Key` を要求する場合 |
| `GPLAY_EMAIL` | Secret | Google Play アカウント認証用 |
| `GPLAY_AAS_TOKEN` | Secret | AAS token 認証用 |
| `GPLAY_AUTH_TOKEN` | Secret | auth token 認証用 |

endpoint はベース URL または `/api/auth` まで指定でき、複数指定時は順番に試します。レスポンス本文や token を CI エラーへ出さないようにしています。

Google Play が利用できない場合でも、互換性を確認したフォールバックへ進みます。取得元が返したバージョンが要求版と違う場合は、その APK を採用しません。

## VirusTotal

VirusTotal は **パッチ前の元 APK** を検査します。完成 APK は API 消費とアップロード量を抑えるため通常はアップロードしません。

SHA-256 の既存結果を先に照会し、未知のハッシュだけをアップロードします。`malicious` / `suspicious` 判定、API 障害、解析タイムアウトなどで安全確認を完了できない場合、その APK は公開しません。

## Actions

| Workflow | 役割 |
| --- | --- |
| `check-upstream.yml` | 全宣言済みパッチソースと APK 更新を定期確認 |
| `build.yml` | ダウンロード、検証、パッチ、署名、VT、リリース、Issue 報告 |
| `test-build.yml` | `build_all_sources=true` で全有効エントリを手動ビルド |
| `configuration-check.yml` | 設定検証、Python compile、provider validation、unit tests |
| `health-check.yml` | リポジトリ/取得元のヘルスチェック |
| `pr-targeted-build-verification.yml` | 指定アプリの PR 向け実ビルド確認 |

GitHub Actions のスケジュール実行は混雑により遅延する場合があります。

## 設定を変更する

運用者向けの Secrets、Google Play 認証、手動実行方法、トラブルシューティングは [`SETUP.md`](SETUP.md) を参照してください。

主な設定ファイルは次のとおりです。

- `my-patch-config.json` — アプリ/パッチソース/パッチオプション
- `arch-config.json` — アーキテクチャの個別指定
- `sources/*.json` — CLI とパッチバンドルの取得先
- `apps/**.json` — package ID と元 APK 取得設定
- `last-tags.json` — **成功済み**のパッチ/APK 状態

`last-tags.json` はビルド開始時ではなく、対象ビルド・VirusTotal・リリースが完了した後だけ進めます。これにより一時障害で更新を取り逃さず、次回に再試行できます。

## 注意事項

- パッチ適用済み APK は第三者による非公式ビルドです。
- 元アプリの利用規約、ライセンス、地域法令を確認してください。
- 署名鍵が公式 APK と異なるため、公式版から上書き更新できない場合があります。
- VirusTotal で検出がないことは安全性の保証ではありません。
- Google Play や各 APK 配布サイトの仕様変更・アクセス制限により、一時的に取得できなくなることがあります。

## License / Credits

各アプリ、Morphe/ReVanced CLI、パッチセット、取得元サービスの権利はそれぞれの権利者に帰属します。このリポジトリはそれらの公式プロジェクトではありません。
