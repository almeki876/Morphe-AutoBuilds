name: 機能要望 / アプリ追加提案 (Feature Request / App Addition)
description: 新しいアプリの追加やパッチ設定の変更、新機能の提案を行います。
title: '[FEAT] '
labels: ['enhancement']
assignees: ''

body:
  - type: markdown
    attributes:
      value: |
        新機能の提案や対象アプリ追加のリクエストをお寄せいただきありがとうございます。

  - type: textarea
    id: proposal
    attributes:
      label: 提案の内容 (Proposal)
      description: 追加したいアプリや機能について簡潔に説明してください。
      placeholder: 例: 〇〇アプリの自動ビルドに対応してほしい / 〇〇パッチを無効化するオプションを追加してほしい
    validations:
      required: true

  - type: textarea
    id: app_details
    attributes:
      label: アプリ詳細情報 (App Details - アプリ追加の場合)
      description: アプリ名、パッケージID、希望するパッチソースURL、元APKの配布サイト情報等を記入してください。
      placeholder: |
        - アプリ名: Example App
        - パッケージID: com.example.app
        - パッチソース: https://github.com/example/patches
        - 元APK取得元: APKMirror / APKPure

  - type: textarea
    id: rationale
    attributes:
      label: 提案の理由 / 背景 (Rationale / Context)
      description: この提案がなぜ必要か、どのようなメリットがあるかを記入してください。
