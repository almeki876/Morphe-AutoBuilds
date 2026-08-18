name: バグ報告 (Bug Report)
description: ビルド失敗、ダウンロードエラー、アプリ動作不良などの問題を報告します。
title: '[BUG] '
labels: ['bug']
assignees: ''

body:
  - type: markdown
    attributes:
      value: |
        バグ報告をお寄せいただきありがとうございます。問題を迅速に解決するため、以下の情報を可能な限り詳しくご記入ください。

  - type: textarea
    id: summary
    attributes:
      label: 不具合の概要 (Summary)
      description: どのような問題が発生したか簡潔に説明してください。
      placeholder: 例: YouTube Musicのパッチ適用済みAPKで起動時にクラッシュする
    validations:
      required: true

  - type: dropdown
    id: app_name
    attributes:
      label: 対象アプリ (Target App)
      description: 問題が発生したアプリを選択してください。
      options:
        - YouTube
        - YouTube Music
        - AdGuard
        - Prime Video
        - Duolingo
        - ibis Paint X
        - Icon Packer
        - Nova Launcher
        - Proton VPN
        - Smart Launcher
        - SoundCloud
        - WPS Office
        - Crunchyroll
        - GitHub
        - Lightroom Mobile
        - Windy
        - Xodo
        - XRecorder
        - MEGA
        - Proton Mail
        - Disney+
        - Photomath
        - Pixiv
        - Adobe Photoshop Mix
        - Amazon Shopping
        - Google News
        - Google Photos
        - Google Recorder
        - Threads
        - TikTok
        - Tumblr
        - Twitch
        - Viber
        - ゆうちょ通帳アプリ
        - ゆうちょ認証アプリ
        - その他 / その他全般

  - type: textarea
    id: steps
    attributes:
      label: 再現手順 (Steps to Reproduce)
      description: 問題を再現するための手順を記入してください。
      placeholder: |
        1. 最新リリースから APK をダウンロード
        2. 端末へインストールして起動
        3. 〇〇の操作を行うとエラーが発生
    validations:
      required: true

  - type: textarea
    id: expected
    attributes:
      label: 期待される動作 (Expected Behavior)
      description: 本来どう動作するべきかを記入してください。

  - type: textarea
    id: logs
    attributes:
      label: エラーログ・スクリーンショット (Logs / Screenshots)
      description: GitHub Actions のビルドログやエラーメッセージ、スクリーンショットがあれば貼り付けてください。
      render: text

  - type: textarea
    id: environment
    attributes:
      label: 動作環境 (Environment)
      description: ご使用の端末・OSバージョンなどを記入してください。
      placeholder: |
        - OS: Android 14
        - 端末: Pixel 8
        - リリースバージョン: 2026-08-08 18:00 JST
