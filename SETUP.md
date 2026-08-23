# Morphe AutoBuilds 運用セットアップ

この文書は、Morphe AutoBuildsをフォークまたは独自リポジトリで運用する管理者向けです。公開済みAPKを利用するだけの場合は設定不要です。[README](./README.md)から最新リリースを確認してください。

## 必要なもの

- GitHub Actionsを利用できるGitHubリポジトリ
- リポジトリのActionsとSecretsを変更できる権限
- `matchadaisuke/morphe-patches`の読み取りと、運用リポジトリのActions Secrets更新に使えるPersonal Access Token
- VirusTotal Communityまたは上位プランのAPIキー
- Google Play取得用の`gplaydl` APIキーと、公式gplaydl Authenticatorを利用できるGoogleアカウント
- ゆうちょ2アプリをビルドする場合は、日本のGoogle Playで両アプリを入手済みのアカウントと、日本にあるTailscale exit node
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

## 3. SecretsとVariablesを登録する

登録場所は「Settings」→「Secrets and variables」→「Actions」→「Repository secrets」です。

| Secret名 | 用途 |
| --- | --- |
| `PAT` | 非公開のYuzuパッチリポジトリの読み取りと、登録ワークフローからActions Secretsを更新する認証 |
| `VIRUSTOTAL_API_KEY` | ダウンロード直後の未加工APKを公開前にVirusTotalで検査 |
| `GPLAYDL_API_KEY` | `gplaydl` 4.2.1によるGoogle Playの詳細取得とダウンロード |
| `GPLAY_EMAIL` | Google Playへリンクしたアカウント。登録ワークフローが保存 |
| `GPLAY_AAS_TOKEN` | 公式Authenticatorで同期したAASトークン。登録ワークフローが保存 |
| `TS_OAUTH_CLIENT_ID` | ゆうちょジョブをTailscaleへ接続するOAuthクライアント |
| `TS_OAUTH_SECRET` | 上記OAuthクライアントのSecret |

ゆうちょ2アプリを除外する場合、Tailscale用の2 Secretsと後述の`TS_EXIT_NODE`は不要です。Google Play認証がない通常アプリは公開プロバイダーへフォールバックできますが、`google-play-only`のアプリは認証や取得に失敗すると必ず停止します。

Repository Variablesは必要に応じて次を登録します。

| Variable名 | 用途 |
| --- | --- |
| `TS_EXIT_NODE` | ゆうちょ取得時に使う日本のTailscale exit node |
| `GPLAYDL_PREFERRED_PROFILE` | `gplaydl`で優先する端末プロファイル。未指定時は自動試行 |

`GPLAY_EMAIL`と`GPLAY_AAS_TOKEN`があれば、実行ごとに一時dispenserを起動します。`GITHUB_TOKEN`はActions実行時にGitHubが自動発行します。更新管理は`last-tags.json`を使用しており、古い`LAST_MORPHE_TAG`などのVariablesも不要です。

### PAT

Fine-grained Personal Access Tokenを使用する場合は、次の条件を満たすようにします。

- Resource ownerが`matchadaisuke/morphe-patches`へアクセスできる所有者である
- Repository accessに`matchadaisuke/morphe-patches`が含まれている
- Repository permissionsの`Contents`が`Read-only`以上である
- Google Play登録ワークフローを使う運用リポジトリでは、Actions Secretsを更新できる権限も持つ
- 有効期限内である

トークンの値はファイル、コミット、Issue、Actionsログへ記載しないでください。

GitHub CLIから登録する場合は、値をコマンド行へ直接書かず、表示される入力欄へ貼り付けます。

```bash
gh secret set PAT
```

この非公開リポジトリへのアクセス権を持たないフォークでは、Yuzuパッチツールを取得できません。現在のビルドはツールを一括準備するため、PATが無効だと他のアプリを含むビルド全体が開始できない場合があります。

### Google Play APIキーとアカウント

まず`GPLAYDL_API_KEY`を登録します。値はコマンド行へ直接書かず、GitHub CLIの入力欄へ貼り付けます。

```bash
gh secret set GPLAYDL_API_KEY
```

次にActionsの「Register Google Play Account」を手動実行し、`expected_email`へ使用するGoogleアカウントを入力します。ジョブ概要に一時的なAuthenticatorサーバーURLが表示されたら、Android端末で公式gplaydl Authenticatorを開き、次の順に操作します。

1. 「Settings」→「Advanced server settings」を開く
2. 「Dispenser URL」へジョブ概要の一時URLを入力し、「Change server」を選ぶ
3. `expected_email`と同じGoogleアカウントを追加する
4. ワークフローが`GPLAY_EMAIL`と`GPLAY_AAS_TOKEN`を保存したことを確認する

一時サーバーとデータベースは登録中だけ起動し、AASトークンをログやアーティファクトへ出しません。登録完了後はAuthenticatorのDispenser URLを普段使用する値へ戻してください。

ゆうちょ通帳・ゆうちょ認証では、同じアカウントを日本のGoogle Playで使用し、両アプリを事前に入手済みにします。さらにTailscaleの`tag:ci`を利用できるOAuthクライアントを`TS_OAUTH_CLIENT_ID`と`TS_OAUTH_SECRET`へ登録し、日本のexit nodeを`TS_EXIT_NODE`へ設定します。ジョブは取得前に外向きIPが日本であることを検証します。

### VirusTotal APIキー

GitHub CLIから登録する場合は次を実行します。

```bash
gh secret set VIRUSTOTAL_API_KEY
```

通常のVirusTotal APIへアップロードしたAPKは、VirusTotalや解析パートナーと共有される場合があります。共有条件を確認したうえで利用してください。未加工の元APKは最初にSHA-256で既存結果を照会し、VirusTotalに未知のハッシュだけをアップロードします。パッチ適用後の完成APKはアップロードしません。

APIキーが未設定、無効、利用上限超過、または解析がタイムアウトした場合、未検査APKを公開しないためリリースジョブは失敗します。

## 4. 設定ファイルを確認する

初回実行前に、少なくとも次のファイルを確認します。

| ファイル | 確認内容 |
| --- | --- |
| `my-patch-config.json` | ビルド対象の`app_name`と`source`、パッチオプション |
| `arch-config.json` | 個別に固定するアーキテクチャ。未指定時はarm64優先 |
| `app-metadata/<app>.json` | 取得元に依存しないpackage IDと`source_policy` |
| `apps/<provider>/<app>.json` | プロバイダー別のpackage ID、検索名、URL、補助バージョン |
| `sources/<source>.json` | CLIとパッチバンドルのGitHubリリース |
| `last-tags.json` | 前回確認したパッチとAPKのバージョン |

パッチCLIが明示したversionNameが互換性の基準です。`any`、`null`、または正常な制約なし結果は最新版を意味しますが、CLIのエラー、空出力、未知の形式を最新版扱いにはしません。明示版に必要なAndroid versionCodeは、APKPure、Uptodown、完全一致するGoogle Play現在版メタデータから実行時に解決します。`apps/`と`app-metadata/`へ`version_code`を固定すると検証エラーになります。

`apps/`の`version`はプロバイダー検索やヘルスチェックを補助する値であり、パッチが返した明示的な互換版を上書きしません。APKMirror設定の`org`やアプリ名が古い場合も、package IDから検索できる取得元があります。

AdGuardは`apps/github/adguard.json`の`primary`かつ`exclusive`な設定により、公式の安定版GitHub Releaseだけを使用します。第三者APKサイトや既存の共通キャッシュへはフォールバックしません。ゆうちょ2アプリは`app-metadata/`で`google-play-only`に設定され、Google Play取得に失敗しても一般APKサイトへ切り替えません。

Anddea版のカスタムアイコン設定は`my-patch-config.json`にあり、YouTubeは`patch-assets/anddea/youtube/xisr_evergreen`、YouTube Musicは`patch-assets/anddea/youtube-music/xisr_yellow`を使用します。パッチ名やオプションキーが上流で変わった場合は、設定検証と対象ビルドの両方で確認してください。

## 5. 初回の動作確認

GitHubの「Actions」タブから、最初に「Build and Release APKs」を手動実行します。全対象なら`build_all_sources=true`、一部なら`updated_sources`へソース名、`updated_apps`へカンマ区切りのアプリ名を指定します。両方を指定した場合は、どちらかに該当する有効な組み合わせをビルドします。従来の`*_updated`と`*_force_build`入力も利用できます。

すべての入力を空または`false`のままにすると、ビルドマトリクスが空になる場合があります。全ソースを簡単に検証する場合は「Trigger Test Build All (Release)」を使えます。

実行中は次の順番で確認します。

1. `Download Build Tools`がすべてのCLIとパッチを取得できる
2. `Prepare Build Matrix`に対象アプリが含まれる
3. 各`Build <app> with <source>`ジョブがAPKを作成する
4. `Scan Unmodified Base APKs with VirusTotal`が取得直後の元APKを検査する
5. `Create Integrated Release`がリリースを作成する

ビルドは同時アクセスによる配布サイトの制限を避けるため、並列数を抑えて実行します。VirusTotal Community APIの待機もあるため、対象数によっては完了まで長時間かかります。

GitHub CLIで状況を見る場合は次を使用できます。

```bash
gh run list --workflow=build.yml --limit=5
gh run watch
```

## 6. 自動更新確認を有効にする

`Check Upstream for Updates`は、毎日09:00 UTC（日本時間18:00頃）に実行されます。GitHub Actionsのスケジュールは混雑により遅延することがあります。

このワークフローは、`sources/`に登録された各パッチツール／バンドルの新しいリリースと、パッチが任意バージョンへ対応するアプリの新しい元APKを確認します。

更新を検出すると、影響するパッチソースのビルドを起動し、確認済みバージョンを`last-tags.json`へ保存します。

`last-tags.json`は更新検出直後には変更されません。全対象のビルド、VirusTotal検査、リリース作成が成功した場合だけ保存されます。一部失敗時は前回状態を維持するため、次回の定期確認で同じ更新を自動再試行できます。

`Repository and APK Provider Health`は毎日03:17 UTC（日本時間12:17頃）に、設定、パッチツール資産、APK取得元を検査します。障害時は`Automated APK build health check failed` Issueを作成または追記し、復旧すると自動で閉じます。レポートはActionsアーティファクトへ30日間保存されます。

DependabotはGitHub ActionsとPython依存関係を週1回確認し、更新をまとめたPull Requestを作成します。Pull Requestでは`Configuration Check`がJSON設定、Python構文、Google Playダウンローダーのコンパイル、プロバイダー登録、ユニットテスト、Uptodown履歴経路を検査します。取得処理へ影響するPRでは`PR Targeted Build Verification`が既知の回帰対象を実ビルドします。

手動確認はActions画面、または次のコマンドから実行できます。

```bash
gh workflow run check-upstream.yml
```

## 7. リリース結果を確認する

正常なリリースには、成功したAPKと次の情報が含まれます。

- パッチソースと解決済みバージョン
- 成功・失敗したアプリとソースの組み合わせ
- 元APKの取得元、バージョン、アーキテクチャ
- 元APKのVirusTotal検出数、照会方法、SHA-256へのリンク

一部のビルドだけ失敗した場合、タイトルへ`Partial`が付き、成功したAPKのみ公開されることがあります。元APKのVirusTotal検査で検出または検査失敗が発生した場合は、APKが完成していてもリリースされません。完成APKはVirusTotalへアップロードしません。

VirusTotalの元APK用MarkdownとJSONレポートは、Actions実行の`virustotal-report`アーティファクトへ30日間保存されます。Markdownには検出エンジンの詳細、JSONには返却された全エンジンのカテゴリ、検出名、方式、バージョン、更新日が入ります。各APKの完了ごとにファイルを更新し、同じ内容の検出警告をActionsログにも出します。

## トラブルシューティング

### `401 Bad credentials`またはPAT認証エラー

- `PAT`がRepository Secretとして登録されているか確認する
- PATの有効期限と失効状態を確認する
- `matchadaisuke/morphe-patches`の`Contents: Read`権限を確認する
- 同じ無効なトークンを再試行せず、新しいトークンへ更新する

### ビルドマトリクスが空になる

手動実行時の`*_updated`と`*_force_build`がすべて`false`になっています。対象ソースのどちらかを`true`にして再実行します。

### 元APKを取得できない

まずGoogle Playの認証と要求版の同定結果を確認します。通常アプリはその後にAPKMirror、APKPure、Uptodown、Softonic、Aptoide、APKComboを試し、さらに補助取得経路へ進みます。対象バージョンが取得元に存在するか、package ID、versionName、versionCodeが一致するかをログで確認してください。

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

### Google Playで認証・購入・版解決に失敗する

- `GPLAYDL_API_KEY`、`GPLAY_EMAIL`、`GPLAY_AAS_TOKEN`が正しいSecret名で登録されているか確認する
- `Register Google Play Account`を再実行し、期限切れ・失効したAASトークンを更新する
- 明示versionNameのversionCodeを解決できない場合、固定値をJSONへ追加せず、公開履歴か同一versionNameのGoogle Play現在版で確認できるまで失敗させる
- ゆうちょでは`TS_EXIT_NODE`の外向きIPが日本か、登録アカウントが日本のGoogle Playで対象アプリを入手済みか確認する
- 購入状態や端末プロファイルを調べる場合は「Diagnose Google Play Purchase」を手動実行する

ゆうちょ2アプリは意図的に第三者ミラーを使いません。認証や地域制限を回避するために`source_policy`を緩めるのではなく、アカウントの取得状態、日本経由、端末プロファイルを修正してください。

### VirusTotalで停止する

- Secret名が正確に`VIRUSTOTAL_API_KEY`になっているか確認する
- APIキーが有効か、利用上限に達していないか確認する
- `virustotal-report`とジョブ概要で対象APKの結果を確認する
- 元APKに`malicious`または`suspicious`が1件以上ある場合は、原因を確認するまで公開しない

現在の公開判定でVirusTotalへ送るのは未加工の元APKです。パッチ適用・再署名後の完成APKはAPI消費と処理時間を抑えるためアップロードしません。

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
- PATは非公開パッチの`Contents: Read`と、運用リポジトリで登録ワークフローに必要なActions Secrets更新だけに絞り、期限を設定してください。
- Google Playのメールアドレス、AASトークン、APIキー、Tailscale OAuth情報をログ、Issue、アーティファクトへ載せないでください。
- パッチソース、配布サイト、VirusTotal APIは外部サービスです。仕様変更時はActionsログと各サービスの公式情報を確認してください。
- 公開APKの「検出なし」は安全性の保証ではありません。
- package ID、署名、取得元、ハッシュを確認できないAPKは公開しないでください。
