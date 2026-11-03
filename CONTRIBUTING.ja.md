# コントリビューションガイド

[English](CONTRIBUTING.md) | 日本語

Morphe AutoBuilds の改善にご協力いただきありがとうございます。バグ報告、ドキュメント修正、テスト、範囲を絞ったコード変更を歓迎します。

## 変更を始める前に

- 利用者向け情報は [README（日本語）](README.ja.md)、リポジトリの動作や運用設定は [SETUP.ja.md](SETUP.ja.md) を確認してください。
- 重複を避けるため、既存の [Issue](https://github.com/almeki876/Morphe-AutoBuilds/issues) と Pull Request を検索してください。
- 大きな変更や新機能は、作業前に Issue を作成して範囲を相談してください。
- APK、認証情報、アクセストークン、個人情報、他者の非公開情報をコミットや Issue 添付に含めないでください。ログやスクリーンショットを共有する前に秘密情報を伏せてください。

## 問題の報告・改善提案

**Issues → New issue** から内容に合うフォームを選んでください。日本語・英語どちらの報告も歓迎します。該当する場合は、アプリ、パッチソース、Release または workflow run、期待した動作、実際の動作、再現手順を記載してください。

添付するのは必要最小限のスクリーンショットやログにしてください。Issue の説明欄へのドラッグ＆ドロップ、貼り付け、または GitHub の添付機能で追加できます。APK本体はアップロードせず、該当する Release へのリンクを記載してください。

## 変更の進め方

1. リポジトリを fork し、変更用の branch を作成します。
2. 変更範囲を絞り、既存の Python、JSON、YAML、ドキュメントの書式に合わせます。
3. 動作を変更した場合はテストを追加・更新します。生成APK、秘密情報、ローカルビルド成果物はコミットしないでください。
4. Pull Request を作成する前に、以下のチェックを実行します。
5. `main` 向けに Pull Request を作成し、背景、変更内容、テスト結果、利用者への影響を説明します。関連する Issue があればリンクしてください。

## ローカルでのテスト

Python 3.11 を使い、固定された依存パッケージをインストールしてください。

```bash
python3 -m pip install -r requirements.txt
python3 scripts/validate_repository.py
python3 -m compileall -q src scripts
python3 -m unittest discover tests
```

GitHub Actions を変更した場合は、`.github/workflows/` に対して [actionlint](https://github.com/rhysd/actionlint) も実行し、変更した Issue フォームの YAML も検証してください。Pull Request では `Repository Validation` workflow が設定チェックと unit test を実行します。

## Pull Request の確認事項

- レビューしやすい大きさにし、無関係な変更は別の Pull Request に分けてください。
- 動作や手順を変更した場合は、関連する英語・日本語ドキュメントを両方更新してください。
- workflow の権限、起動条件、Secrets、Release の挙動を変更した場合は、その内容を説明してください。
- 実行したテストと、実行できなかったチェックを明記してください。
- 無関係な整形や生成済み Release 成果物を含めないでください。

## セキュリティ上の問題

脆弱性を公開 Issue で報告しないでください。GitHub を通じてメンテナへ非公開で連絡してください。
