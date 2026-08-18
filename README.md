# Morphe AutoBuilds

[日本語] | [English](README.en.md)

[![Upstream Check](https://img.shields.io/github/actions/workflow/status/matchadaisuke/Morphe-AutoBuilds/check-upstream.yml?label=upstream%20check)](https://github.com/matchadaisuke/Morphe-AutoBuilds/actions/workflows/check-upstream.yml)
[![Build Status](https://img.shields.io/github/actions/workflow/status/matchadaisuke/Morphe-AutoBuilds/build.yml?label=build)](https://github.com/matchadaisuke/Morphe-AutoBuilds/actions/workflows/build.yml)
[![Latest Release](https://img.shields.io/github/v/release/matchadaisuke/Morphe-AutoBuilds?label=latest%20release)](https://github.com/matchadaisuke/Morphe-AutoBuilds/releases/latest)

Morphe AutoBuildsは、Androidアプリへコミュニティ製パッチを適用し、インストール可能なAPKを自動作成するリポジトリです。パッチは、元アプリの機能や表示、動作を変更するための差分データです。

GitHub Actionsがパッチや対象アプリの更新を確認し、互換性のある元APKの取得、パッチ適用、署名、VirusTotal検査、GitHub Releasesへの公開までを自動で行います。同じアプリでもパッチ提供元が異なる場合は、それぞれ別のAPKとして作成されます。

> [!IMPORTANT]
> このリポジトリと配布APKは、各アプリの開発元、Google、ReVanced、Morphe、各パッチ作者の公式配布物ではありません。利用前に「安全性と注意事項」を確認してください。

## APKをダウンロードする

完成したAPKは[最新リリース](https://github.com/matchadaisuke/Morphe-AutoBuilds/releases/latest)からダウンロードできます。リリース名には作成日時がJSTで記録されます。

各リリースの説明には、次の情報が掲載されます。

- ビルドに成功・失敗したアプリ
- 使用したパッチソースとバージョン
- 元APKのバージョン、アーキテクチャ、取得元
- VirusTotalによる検査結果とファイルのSHA-256

APKファイル名には、アプリ名、対象アーキテクチャ、元APKバージョンが含まれます。基本的には`arm64-v8a`版が優先され、作成できない場合は`universal`版へ自動的に切り替わります。

## 対象アプリ

現在の設定では、以下の組み合わせをビルドします。

| パッチソース | 対象アプリ |
| --- | --- |
| [Morphe](https://github.com/MorpheApp/morphe-patches) | YouTube、YouTube Music |
| [Anddea](https://github.com/anddea/revanced-patches) | YouTube、YouTube Music |
| [Hoo-dles](https://github.com/rushiranpise/morphe-patches) | AdGuard、Prime Video、Duolingo、ibis Paint X、Icon Packer、Nova Launcher、Proton VPN、Smart Launcher、SoundCloud、WPS Office、Crunchyroll、GitHub、Lightroom Mobile、Windy、Xodo、XRecorder、ゆうちょ通帳、ゆうちょ認証 |
| [Tosox](https://github.com/Tosox/revanced-patches) | MEGA |
| [RookieEnough](https://github.com/RookieEnough/De-Vanced) | Proton Mail、Disney+、Photomath、Pixiv、Adobe Photoshop Mix、Amazon Shopping、Google News、Google Photos、Google Recorder、Threads、TikTok、Tumblr、Twitch、Viber |
| [YuzuMikan404](https://github.com/YuzuMikan404) | ゆうちょ通帳、ゆうちょ認証 (元APK取得元) |

対象やパッチ内容はアップストリームの変更に応じて変わることがあります。実際に適用された内容は、各リリースとGitHub Actionsのログを確認してください。

## 自動ビルドの流れ

このリポジトリは毎日18:00頃（JST）に、登録されたパッチソースと監視対象APKの更新を確認します。GitHub Actionsのスケジュール実行は混雑状況により遅れる場合があります。

別の定期ヘルスチェックが、設定全体、パッチツールのリリース資産、各APKの代替取得元を毎日検査します。全取得元が使えないアプリやツール取得障害が見つかると、診断レポートを保存してIssueを自動作成または更新し、復旧後はIssueを自動で閉じます。

更新が見つかると、次の処理が実行されます。

1. 使用するパッチツールとパッチバンドルのバージョンを確定する
2. パッチが対応する元APKバージョンを調べる
3. 複数の取得元から元APKをダウンロードする
4. APKの形式とダウンロード内容を検証する
5. 取得した未加工の元APKを検査用に保存する
6. パッチを適用し、リポジトリのキーストアで署名する
7. 保存した元APKをVirusTotalで検査する
8. 元APKの検査を通過した場合だけ、取得元情報付きのリリースを公開する

一部のアプリだけビルドに失敗した場合は、成功したAPKのみを部分リリースとして公開することがあります。APKが1件も完成しなかった場合やVirusTotal検査を完了できなかった場合は、リリースを作成しません。

## 元APKの取得方法

通常は、パッチと互換性のあるバージョンを次の順番で探します。

1. APKMirror
2. APKPure
3. Uptodown
4. Softonic
5. Aptoide
6. APKCombo

サイト別の設定がなくても、既存設定のpackage IDを利用して検索できる取得元があります。通信エラー、レート制限、不完全なダウンロード、HTMLの誤取得、壊れたAPKを検出した場合は、その取得元を失敗として次へ進みます。

パッチツールが`88600 (8.8.6)`のようにversion codeとversion nameを返す場合は、両方を保持し、取得元のAPIや画面に合う識別子を使います。APKPureは通常のWeb画面がCloudflare検証で利用できない場合、`d.apkpure.net`のAPK直接配信エンドポイントを先に試します。対話操作が必要なボット検証を検出した場合は、同じ画面を繰り返し要求せず次の取得元へ進みます。

APKMirrorでは、アプリトップに表示されない少し古い互換版も、設定済みpublisher/app slugからrelease URLを直接検証して探します。最新版の監視は全アプリで`name`から生成した専用の`/uploads/?appcategory=<name>`を最初に使い、対象アプリのreleaseリンクだけを解析します。ページ内に混在する別アプリや広告の版番号は採用しません。APKMirrorのvariant表でversion codeを取得できた場合は同じビルド中のAPKPureフォールバックへ引き継ぐため、APKMirrorの最終ダウンロードだけが403になっても同一バージョンを取得できます。

AdGuardは、公式の[AdguardTeam/AdguardForAndroid](https://github.com/AdguardTeam/AdguardForAndroid)にある最新の安定版GitHub Releaseからのみ取得します。通常版APKをバージョン込みのファイル名で特定し、Android TV版、プレリリース、第三者配布APK、取得元を確認できないキャッシュを選びません。公式GitHubから取得できない場合は、別サイトのAPKで続行せずビルドを停止します。

ゆうちょ通帳アプリとゆうちょ認証アプリは、[YuzuMikan404/Yuucho-Tuucho-and-Ninsho](https://github.com/YuzuMikan404/Yuucho-Tuucho-and-Ninsho)のGitHubリリースを最優先にします。対象APKが見つからない場合のみ、上記6サイトを順番に試します。

一度正常に取得できた元APKは、ハッシュ検証付きの内部キャッシュへ保存されることがあります。次回以降もpackage IDとバージョンが完全に一致する場合だけ再利用されます。

## VirusTotal検査

取得直後に保存した未加工の元APKをVirusTotalで検査します。パッチ適用・署名後の完成APKは、アップロード時間とAPI消費を抑えるため検査対象にしません。

各ファイルは最初にSHA-256でVirusTotalの既存結果を照会します。既存結果があればアップロードや再解析を行わずその結果を利用し、未知のハッシュだけをアップロードして解析します。次のいずれかに該当する場合は、公開を中止します。

- 未加工の元APKに`malicious`または`suspicious`の判定が1件以上ある
- APIエラーや利用上限により解析を完了できない
- 解析がタイムアウトする
- 完了した検査エンジンの判定を取得できない

元APKの検査結果はリリースノートに掲載され、Actionsのレポートとしても保存されます。検出時はエンジン名、カテゴリ、検出名、エンジンバージョン、定義更新日をActionsログとMarkdownへ出し、全エンジンの詳細をJSONアーティファクトへ保存します。レポートはAPKごとに更新するため、長時間の検査が途中で失敗した場合も完了済み分を確認できます。VirusTotalで検出がないことは、元APKや完成APKが完全に安全であることを保証するものではありません。

## インストールの基本

1. [最新リリース](https://github.com/matchadaisuke/Morphe-AutoBuilds/releases/latest)を開き、目的のアプリとパッチソースが含まれるAPKを選ぶ
2. リリースノートで元APKの取得元、バージョン、VirusTotal結果を確認する
3. 必要に応じて既存アプリのデータをバックアップする
4. Android端末でAPKを開き、画面の案内に従ってインストールする

Androidの設定によっては、ブラウザやファイル管理アプリに「不明なアプリのインストール」を一時的に許可する必要があります。インストール後は不要な許可を戻すことを推奨します。

## インストール前の注意

- 配布APKは元アプリとは異なる署名になるため、公式版へそのまま上書きインストールできない場合があります。
- 既存アプリの削除が必要な場合は、先にデータや設定をバックアップしてください。
- YouTubeなどでGoogleアカウントへログインするには、別途GmsCore系アプリが必要になる場合があります。
- パッチ適用アプリの利用が、元アプリの利用規約や組織のセキュリティ方針に反する可能性があります。
- 金融・認証アプリの改変版には特に大きなリスクがあります。仕組みと公開情報を理解できない場合は、公式アプリを利用してください。

## このリポジトリを運用する場合

通常の利用者は、リポジトリをセットアップする必要はありません。フォークや独自環境で自動ビルドを運用する場合は、[SETUP.md](./SETUP.md)を参照してください。

主な設定は次のファイルに分かれています。

| ファイルまたはディレクトリ | 内容 |
| --- | --- |
| `my-patch-config.json` | ビルドするアプリ、パッチソース、パッチオプション |
| `arch-config.json` | アプリごとのアーキテクチャ指定 |
| `apps/` | 元APKのpackage IDと取得元設定 |
| `sources/` | パッチツールとパッチバンドルの取得設定 |
| `scripts/probe_apk_sources.py` | 指定版を各取得元で実通信確認する診断コマンド |
| `scripts/validate_repository.py` | JSON、package ID、source、architectureの整合性検査 |
| `scripts/provider_health.py` | 全対象アプリの取得元を実通信で定期検査 |
| `.github/workflows/` | 更新確認、ビルド、検査、リリースの自動処理 |

## 免責事項

このリポジトリは、各アプリおよびパッチプロジェクトと提携していない非公式プロジェクトです。配布物の利用、アカウント、端末、データ、サービス利用条件に関する判断と責任は利用者にあります。

問題を報告する場合は、秘密情報や個人情報を除いたうえで[Issues](https://github.com/matchadaisuke/Morphe-AutoBuilds/issues)へ実行日時、対象アプリ、失敗したステップを記載してください。
