# Morphe AutoBuilds - セットアップガイド

このガイドに従い、Morphe AutoBuilds システムを GitHub リポジトリで運用するための設定を行ってください。

## 📋 前提条件

- GitHub リポジトリが作成済み
- GitHub CLI (`gh`) がインストール済み
- リポジトリへの管理者権限がある

## 🔧 セットアップ手順

### ステップ 1: リポジトリに新規ファイルを push

```bash
# リポジトリをクローン
git clone https://github.com/YOUR_USERNAME/morphe-autobuilds.git
cd morphe-autobuilds

# 新規ファイルをコピー
cp my-patch-config.json .
cp arch-config.json .
cp scripts/generate_readme.py scripts/
cp .github/workflows/check-upstream.yml .github/workflows/
cp .github/workflows/build.yml .github/workflows/

# 変更をコミット
git add .
git commit -m "feat: implement upstream-triggered build system with JST-tagged releases"
git push origin main
```

### ステップ 2: Repository Variables を初期化

GitHub CLI で Variables を設定します：

```bash
# Morphe の最新タグを取得して設定
morphe_tag=$(gh api repos/MorpheApp/morphe-patches/releases/latest --jq '.tag_name')
gh variable set LAST_MORPHE_TAG --body "$morphe_tag"
echo "✅ LAST_MORPHE_TAG set to: $morphe_tag"

# Anddea の最新タグを取得して設定
anddea_tag=$(gh api repos/anddea/revanced-patches/releases/latest --jq '.tag_name')
gh variable set LAST_ANDDEA_TAG --body "$anddea_tag"
echo "✅ LAST_ANDDEA_TAG set to: $anddea_tag"
```

**GUI での設定方法**:
1. リポジトリの Settings ページを開く
2. 左メニューから「Variables and secrets」→「Variables」を選択
3. 「New repository variable」をクリック
4. 以下の2つを追加：
   - `LAST_MORPHE_TAG`: 最新の Morphe タグ（例: `v1.13.2`）
   - `LAST_ANDDEA_TAG`: 最新の Anddea タグ（例: `v5.4.0-all`）

### ステップ 3: GitHub Actions を有効化

1. リポジトリの Settings ページを開く
2. 左メニューから「Actions」→「General」を選択
3. 「Allow all actions and reusable workflows」を選択
4. 「Save」をクリック

### ステップ 4: 手動テスト（build.yml）

```bash
# build.yml を手動トリガー
gh workflow run build.yml -f morphe_tag="latest" -f anddea_tag="latest"

# ワークフロー実行状況を確認
gh run list --workflow=build.yml --limit=1
```

**期待される結果**:
- 4つの APK がビルドされる
- JST 日時タグ（例: `2026-04-21_15-30-JST`）でリリースが作成される
- README.md と全 APK がリリースアセットに含まれる

### ステップ 5: check-upstream.yml テスト

```bash
# check-upstream.yml を手動トリガー
gh workflow run check-upstream.yml

# ワークフロー実行状況を確認
gh run list --workflow=check-upstream.yml --limit=1
```

**期待される結果**:
- 最新の Morphe / Anddea タグが取得される
- Variables と比較される
- 差分がなければ「No updates found」とログに出力
- 差分があれば build.yml が自動トリガー

### ステップ 6: PAT（Personal Access Token）設定（必須）

yuzu パッチは別のプライベートリポジトリ
`matchadaisuke/morphe-patches` から取得するため、標準の
`GITHUB_TOKEN` ではアクセスできません。このリポジトリを読み取れる PAT が必要です。

1. GitHub で PAT を生成：
   - Settings → Developer settings → Personal access tokens → Fine-grained tokens
   - Repository access は `matchadaisuke/morphe-patches` のみに限定
   - Repository permissions の `Contents` を `Read-only` に設定
   - 有効期限を設定し、期限前にローテーション
   - 「Generate token」をクリック
   - トークンをコピー

2. リポジトリの Secrets に登録：
   ```bash
   gh secret set PAT --body "YOUR_PAT_HERE"
   ```

3. ワークフローファイルで使用：
   ```yaml
   env:
     PAT: ${{ secrets.PAT }}
   ```

PAT を更新した後は `Build and Release APKs` を再実行し、`Download Build
Tools` の yuzu 取得が成功することを確認してください。401 `Bad credentials`
はトークンの失効・削除・入力不備を示すため、同じトークンの再試行では復旧しません。

### ステップ 7: 既存ワークフロー削除（オプション）

古い `patch.yml` を削除または無効化：

```bash
# ファイルを削除
rm .github/workflows/patch.yml

# または、cron トリガーをコメントアウト
# schedule:
#   - cron: '0 6 * * *'

# 変更をコミット
git add .
git commit -m "chore: remove legacy patch.yml workflow"
git push origin main
```

## 📊 動作確認チェックリスト

以下の項目を確認して、システムが正常に動作していることを確認してください：

- [ ] Repository Variables が設定されている
  ```bash
  gh variable list
  ```

- [ ] build.yml が手動実行でき、4つの APK がビルドされる

- [ ] check-upstream.yml が手動実行でき、タグ取得ができる

- [ ] リリースが JST 日時タグで作成されている
  ```bash
  gh release list
  ```

- [ ] リリースアセットに README.md と 4つの APK が含まれている
  ```bash
  gh release view <TAG> --json assets
  ```

- [ ] スケジュール実行（毎日 6:00 UTC）が有効になっている
  - GitHub UI で Workflows ページを確認

## 🔍 トラブルシューティング

### 問題: ワークフローが失敗する

**原因**: GitHub Actions の権限不足

**対処**:
1. リポジトリの Settings → Actions → General を確認
2. 「Allow all actions and reusable workflows」が選択されているか確認
3. ワークフローファイルの `permissions` セクションが正しいか確認

### 問題: Variables が更新されない

**原因**: PAT の権限不足または未設定

**対処**:
1. PAT が `repo` スコープを持っているか確認
2. `PAT` が Secrets に登録されており、有効期限内か確認
3. PAT が `matchadaisuke/morphe-patches` を読み取れるか確認
4. ワークフロー内で `PAT: ${{ secrets.PAT }}` を使用しているか確認

### 問題: APK ビルドが失敗する

**原因**: ツールのダウンロード失敗またはビルドエラー

**対処**:
1. ワークフロー実行ログを確認
2. ツールの URL が正しいか確認（GitHub Releases から手動でダウンロード可能か）
3. `src/__main__.py` の Morphe CLI 引数が正しいか確認

### 問題: リリースが作成されない

**原因**: APK ビルド失敗またはアセットアップロード失敗

**対処**:
1. ワークフロー実行ログで「Build APK」ステップを確認
2. 「Collect All APKs」ステップで APK が見つかっているか確認
3. GitHub の API レート制限に達していないか確認

## 📞 サポート

問題が解決しない場合は、以下を確認してください：

1. [GitHub Actions Documentation](https://docs.github.com/en/actions)
2. [GitHub CLI Manual](https://cli.github.com/manual/)
3. ワークフロー実行ログの詳細メッセージ

## 📝 参考資料

- [実装ドキュメント](IMPLEMENTATION.md)
- [設計書 v2.0](morphe-autobuild-設計書_v2.docx)
