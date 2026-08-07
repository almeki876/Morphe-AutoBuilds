# Morphe AutoBuilds 運用セットアップ

この文書は、Morphe AutoBuildsをフォークまたは独自リポジトリで運用する管理者向けです。公開済みAPKを利用するだけの場合は設定不要です。[README](./README.md)から最新リリースを確認してください。

## 必要なもの

- GitHub Actionsを利用できるGitHubリポジトリ
- リポジトリのActionsとSecretsを変更できる権限
- `matchadaisuke/morphe-patches`を読み取れるPersonal Access Token
- VirusTotal Communityまたは上位プランのAPIキー
- コマンド操作を行う場合は[GitHub CLI](https://cli.github.com/)のログイン済み環境

このリポジトリはPython 3.11、Java 21、GitHub CLIなどをActions内で準備します。通常はローカル環境へビルドツールを導入する必要はありません。

## 1. リポジトリを用意する

GitHub上でこのリポジトリをフォークするか、内容を管理対象リポジトリへpushします。

Actionsが次の操作を行うため、対象ブランチの保護ルールも確認してください。

- Actionsワークフローの実行
- GitHub Releaseとタグの作成
- 内部APKキャッシュ用ドラフトリリースの更新
- `last-tags.json`の自動更新とpush

ブランチ保護でActionsからのpushを禁止している場合、更新確認は成功しても`last-tags.json`の保存に失敗します。

## 2. GitHub Actionsを有効にする

リポジトリの「Settings」から「Actions」→「General」を開き、使用しているActionsを実行できる設定にします。

ワークフローには必要な`permissions`が個別に定義されています。ただし、組織ポリシーやブランチ保護はワークフロー設定より優先されるため、次の権限が許可されていることを確認してください。

- Actionsからリポジトリ内容を読み取る
- リリース、タグ、ドラフトリリースを作成・更新する
- `check-upstream.yml`から`build.yml`を起動する
- `github-actions[bot]`が`last-tags.json`を更新する
- 定期ヘルスチェックが障害Issueを作成、更新、クローズする

## 3. 必須Secretsを登録する

登録場所は「Settings」→「Secrets and variables」→「Actions」→「Repository secrets」です。

| Secret名 | 用途 |
| --- | --- |
| `PAT` | 非公開のYuzuパッチリポジトリ`matchadaisuke/morphe-patches`の読み取り |
| `VIRUSTOTAL_API_KEY` | 完成APKを公開前にVirusTotalで検査 |

`GITHUB_TOKEN`はActions実行時にGitHubが自動発行するため、手動登録は不要です。現在の更新管理は`last-tags.json`を使用しており、古い手順にあった`LAST_MORPHE_TAG`などのRepository Variablesも不要です。

### PAT

Fine-grained Personal Access Tokenを使用する場合は、次の条件を満たすようにします。

- Resource ownerが`matchadaisuke/morphe-patches`へアクセスできる所有者である
- Repository accessに`matchadaisuke/morphe-patches`が含まれている
- Repository permissionsの`Contents`が`Read-only`以上である
- 有効期限内である

トークンの値はファイル、コミット、Issue、Actionsログへ記載しないでください。

GitHub CLIから登録する場合は、値をコマンド行へ直接書かず、表示される入力欄へ貼り付けます。

```bash
gh secret set PAT
```

この非公開リポジトリへのアクセス権を持たないフォークでは、Yuzuパッチツールを取得できません。現在のビルドはツールを一括準備するため、PATが無効だと他のアプリを含むビルド全体が開始できない場合があります。

### VirusTotal APIキー

GitHub CLIから登録する場合は次を実行します。

```bash
gh secret set VIRUSTOTAL_API_KEY
```

通常のVirusTotal APIへアップロードしたAPKは、VirusTotalや解析パートナーと共有される場合があります。共有条件を確認したうえで利用してください。

APIキーが未設定、無効、利用上限超過、または解析がタイムアウトした場合、未検査APKを公開しないためリリースジョブは失敗します。

## 4. 設定ファイルを確認する

初回実行前に、少なくとも次のファイルを確認します。

| ファイル | 確認内容 |
| --- | --- |
| `my-patch-config.json` | ビルド対象の`app_name`と`source`、パッチオプション |
| `arch-config.json` | 個別に固定するアーキテクチャ。未指定時はarm64優先 |
| `apps/<provider>/<app>.json` | package ID、取得先の名前やURL、固定バージョン |
| `sources/<source>.json` | CLIとパッチバンドルのGitHubリリース |
| `last-tags.json` | 前回確認したパッチとAPKのバージョン |

`apps/`の`version`が空の場合は、パッチが対応するバージョンを自動選択します。APKMirror設定の`org`やアプリ名が古い場合も、package IDによる検索を試みます。

AdGuardは`apps/github/adguard.json`の設定で公式の安定版GitHub Releaseだけを使用します。取得元を保証するため、第三者APKサイトや既存の共通キャッシュへはフォールバックしません。ゆうちょ2アプリは`apps/github/`の設定でYuzuMikan404の専用GitHubリリースを優先し、取得できない場合は一般APKサイトへフォールバックします。

## 5. 初回の動作確認

GitHubの「Actions」タブから、最初に「Build and Release APKs」を手動実行します。手動実行では、ビルドしたいパッチソースの`*_updated`または`*_force_build`を`true`にしてください。すべて`false`のままだとビルドマトリクスは空になります。

全ソースを確認する場合は、各ソースの更新フラグを`true`にします。Yuzuを確認する場合は`yuzu_updated`または`yuzu_force_build`も明示的に`true`にしてください。

実行中は次の順番で確認します。

1. `Download Build Tools`がすべてのCLIとパッチを取得できる
2. `Prepare Build Matrix`に対象アプリが含まれる
3. 各`Build <app> with <source>`ジョブがAPKを作成する
4. `Scan Final APKs with VirusTotal`が全APKの解析を完了する
5. `Create Integrated Release`がリリースを作成する

ビルドは同時アクセスによる配布サイトの制限を避けるため、並列数を抑えて実行します。VirusTotal Community APIの待機もあるため、対象数によっては完了まで長時間かかります。

GitHub CLIで状況を見る場合は次を使用できます。

```bash
gh run list --workflow=build.yml --limit=5
gh run watch
```

## 6. 自動更新確認を有効にする

`Check Upstream for Updates`は、毎日09:00 UTC（日本時間18:00頃）に実行されます。GitHub Actionsのスケジュールは混雑により遅延することがあります。

このワークフローは次を確認します。

- Morphe、Anddea、Piko、hoo-dles、RookieEnough、Tosox、Dropped-Patchesの新しいリリース
- パッチが任意バージョンへ対応するアプリの新しい元APK

更新を検出すると、影響するパッチソースのビルドを起動し、確認済みバージョンを`last-tags.json`へ保存します。

`last-tags.json`は更新検出直後には変更されません。全対象のビルド、VirusTotal検査、リリース作成が成功した場合だけ保存されます。一部失敗時は前回状態を維持するため、次回の定期確認で同じ更新を自動再試行できます。

`Repository and APK Provider Health`は毎日03:17 UTC（日本時間12:17頃）に、設定、パッチツール資産、APK取得元を検査します。障害時は`Automated APK build health check failed` Issueを作成または追記し、復旧すると自動で閉じます。レポートはActionsアーティファクトへ30日間保存されます。

DependabotはGitHub ActionsとPython依存関係を週1回確認し、更新をまとめたPull Requestを作成します。Pull Requestでは`Configuration Check`がJSON設定、Python構文、プロバイダー登録を検査します。

手動確認はActions画面、または次のコマンドから実行できます。

```bash
gh workflow run check-upstream.yml
```

## 7. リリース結果を確認する

正常なリリースには、成功したAPKと次の情報が含まれます。

- パッチソースと解決済みバージョン
- 成功・失敗したアプリとソースの組み合わせ
- 元APKの取得元、バージョン、アーキテクチャ
- VirusTotalの検出数とSHA-256へのリンク

一部のビルドだけ失敗した場合、タイトルへ`Partial`が付き、成功したAPKのみ公開されることがあります。VirusTotalで検出または検査失敗が発生した場合は、APKが完成していてもリリースされません。

VirusTotalのMarkdownとJSONレポートは、Actions実行の`virustotal-report`アーティファクトへ30日間保存されます。

## トラブルシューティング

### `401 Bad credentials`またはPAT認証エラー

- `PAT`がRepository Secretとして登録されているか確認する
- PATの有効期限と失効状態を確認する
- `matchadaisuke/morphe-patches`の`Contents: Read`権限を確認する
- 同じ無効なトークンを再試行せず、新しいトークンへ更新する

### ビルドマトリクスが空になる

手動実行時の`*_updated`と`*_force_build`がすべて`false`になっています。対象ソースのどちらかを`true`にして再実行します。

### 元APKを取得できない

対象バージョンが各配布サイトに存在するか、package IDが正しいかを確認します。ワークフローはAPKMirror、APKPure、Uptodown、Softonic、Aptoide、APKComboの順に試すため、最後に記録された各取得元のエラーを確認してください。

403、429、503はアクセス制限や一時障害の可能性があります。不完全なファイルやHTMLが返った場合は自動的に拒否されます。

ローカルで取得元を実通信確認する場合は、依存関係を導入した環境で次のように実行します。`--download-dir`を省略するとAPK全体は保存せず、先頭バイトとHTTP応答だけを検査します。

```bash
python scripts/probe_apk_sources.py \
  --app nova \
  --version 8.8.6 \
  --code 88600 \
  --arch arm64-v8a
```

実APKまで検査する場合は、Git管理外の一時ディレクトリを指定します。

```bash
python scripts/probe_apk_sources.py \
  --app icon-packer \
  --version 1.21.0-release \
  --providers apkpure \
  --download-dir temp/provider-probe
```

Cloudflareの`cf-mitigated: challenge`や対話型検証画面は、Actions上での自動突破を試みません。そのホストを同一ジョブ内で一時停止し、次の取得元へ切り替えます。

APKMirrorは短時間の連続アクセスにより403または429を返すことがあります。ビルドではジョブ開始を15～45秒分散し、同一ジョブ内のAPKMirrorページ要求を3.5秒以上空けます。診断コマンドを連続実行して制限された場合は、同じURLを即座に繰り返さず時間を空けてください。

APKMirrorのreleaseページは読めても最終`download.php`だけが403になる場合があります。この場合はvariant表から得たversion codeをAPKPureの直接配信へ自動的に引き継ぎます。APKPure個別設定がないアプリも、他プロバイダーにpackage IDがあれば実行時設定を生成します。

### VirusTotalで停止する

- Secret名が正確に`VIRUSTOTAL_API_KEY`になっているか確認する
- APIキーが有効か、利用上限に達していないか確認する
- `virustotal-report`とジョブ概要で対象APKの結果を確認する
- `malicious`または`suspicious`が1件以上ある場合は、原因を確認するまで公開しない

### リリースが作成されない

次のいずれかが原因です。

- APKが1件も完成していない
- VirusTotal検査が失敗または検出ありで終了した
- `contents: write`が組織ポリシーで禁止されている
- タグやリリースを作成する権限がない

### `last-tags.json`を更新できない

ブランチ保護、Ruleset、Actionsの書き込み権限を確認します。ワークフローは`github-actions[bot]`として`last-tags.json`だけをコミットします。

## 運用上の注意

- Secretsをリポジトリ内のファイルへ保存しないでください。
- PATは対象リポジトリの読み取りだけに絞り、期限を設定してください。
- パッチソース、配布サイト、VirusTotal APIは外部サービスです。仕様変更時はActionsログと各サービスの公式情報を確認してください。
- 公開APKの「検出なし」は安全性の保証ではありません。
- package ID、署名、取得元、ハッシュを確認できないAPKは公開しないでください。
