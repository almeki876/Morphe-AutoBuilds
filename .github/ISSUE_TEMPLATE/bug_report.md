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
      placeholder: '例: YouTube Musicのパッチ適用済みAPKで起動時にクラッシュする'
    validations:
      required: true

  - type: dropdown
    id: app_name
    attributes:
      label: 対象アプリ (Target App)
      description: 問題が発生したアプリを選択してください。
      options:
        - 1.1.1.1
        - AccuBattery
        - Adobe Acrobat
        - Adobe Scan
        - Alarmy
        - AliExpress
        - Amazon Music
        - Amazon Shopping
        - Brave
        - Brave Beta
        - Brave Nightly
        - Call Recorder
        - CamScanner
        - Countdown Widget
        - Crunchyroll
        - Disney+ (Android TV)
        - YouTube
        - YouTube Music
        - AdGuard
        - Prime Video
        - Prime Video (Android TV)
        - Duolingo
        - Excel
        - File Manager
        - Fing
        - FolderSync
        - Gboard
        - GitHub
        - Google News
        - Google Photos
        - Google Recorder
        - ibis Paint X
        - Icon Packer
        - iLovePDF
        - InShot
        - Kahoot
        - KineMaster
        - KineStop
        - Lightroom Mobile
        - LINE
        - MEGA
        - Netflix
        - Ninja VPN
        - Nova Launcher
        - Adobe Photoshop Mix
        - Photomath
        - Pixiv
        - Poweramp
        - Proton Mail
        - Proton VPN
        - SD Maid SE
        - Sleep as Android
        - Smart Launcher
        - SoundCloud
        - Speedtest
        - Threads
        - TikTok
        - Tumblr
        - Twitch
        - Twitch (Android TV)
        - Viber
        - Windscribe VPN
        - Windy
        - Word
        - WPS Office
        - Xodo
        - XRecorder
        - ゆうちょ通帳アプリ
        - ゆうちょ認証アプリ
        - その他 / その他全般

  - type: input
    id: patch_source
    attributes:
      label: パッチソース (Patch Source)
      description: リリースのファイル名または説明にあるパッチソース名を記入してください。
      placeholder: '例: morphe / revanced-anddea / rushiranpise'

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
        - リリースバージョン: 2026-08-23 18:00 JST
