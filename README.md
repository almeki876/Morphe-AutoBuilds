# Morphe AutoBuilds

[日本語] | [English](README.en.md)

[![Build Status](https://img.shields.io/github/actions/workflow/status/almeki876/Morphe-AutoBuilds/build.yml?label=build)](https://github.com/almeki876/Morphe-AutoBuilds/actions/workflows/build.yml)
[![Latest Release](https://img.shields.io/github/v/release/almeki876/Morphe-AutoBuilds?label=latest%20release)](https://github.com/almeki876/Morphe-AutoBuilds/releases/latest)

Morphe AutoBuilds は、Android アプリへコミュニティ製パッチを適用した APK を自動作成して公開するリポジトリです。

> [!IMPORTANT]
> このリポジトリと配布 APK は、各アプリの開発元、Google、ReVanced、Morphe、各パッチ作者の公式配布物ではありません。利用は自己責任で行ってください。

## APKをダウンロードする

用途に合わせて次の方法を利用できます。

- **[最新リリース](https://github.com/almeki876/Morphe-AutoBuilds/releases/latest)** — その回に作成された APK をまとめて確認
- **[APK直リンク一覧](./Morphe-AutoBuilds-Direct-Download.md)** — アプリ・パッチソース・アーキテクチャごとの最新版へ直接アクセス
- **[Obtainium / ObtainX 用リンク一覧](./Morphe-AutoBuilds-Obtainium.md)** — 対応アプリを更新管理アプリへ登録

直リンク一覧が、現在配布できるアプリとパッチソースの組み合わせを確認する一番簡単な方法です。

## どのAPKを選べばいい？

同じアプリに複数のアーキテクチャがある場合は、端末に合うものを選んでください。

- `arm64-v8a` — 現在の一般的な Android 端末向け
- `armeabi-v7a` — 一部の古い 32bit ARM 端末向け
- `x86_64` / `x86` — 対応するエミュレーターや端末向け
- `universal` — 複数アーキテクチャを含む汎用版

分からない場合は、通常は `arm64-v8a`、用意されていなければ `universal` を選んでください。

## パッチソースについて

同じアプリでも、Morphe、Anddea、rushiranpise など異なるパッチ提供元の APK が存在する場合があります。適用される機能や挙動はパッチソースごとに異なります。

実際に配布中の組み合わせは [APK直リンク一覧](./Morphe-AutoBuilds-Direct-Download.md) を確認してください。

## 安全性について

公開前に、取得した**未加工の元 APK**を VirusTotal で確認します。検査を完了できない場合は、そのビルドを正常なリリースとして公開しません。

ただし、VirusTotal の結果や自動検査だけで完全な安全性を保証することはできません。インストール前にリリース内容を確認し、必要に応じて自身でも検査してください。

パッチ適用済み APK は元アプリとは異なる署名になります。そのため、公式版や別の署名でインストール済みの同一パッケージとは、そのまま上書きできない場合があります。

## 更新について

パッチソースや対象アプリの更新を定期的に確認し、必要な組み合わせだけを自動で再ビルドします。

一部のアプリだけ失敗した場合でも、正常に完成し安全確認を通過した APK は公開されることがあります。個別の失敗は GitHub Issues / Actions で確認できます。

## 問題を報告する

GitHub の **Issues → New issue** から、問題の種類に合うフォームを選んでください。

ビルド・ダウンロード、インストール・アプリ動作、Actions・Release、その他の問題を分けて報告できます。分かる範囲だけで構いませんが、対象アプリ、パッチソース、発生した内容、利用したリリースがあると確認しやすくなります。

## 開発・運用する方へ

このリポジトリをフォークして運用する方法、Secrets、Google Play、Tailscale、VirusTotal、設定ファイル、GitHub Actions の構成などは **[SETUP.md](./SETUP.md)** にまとめています。

README は APK 利用者向け、SETUP は開発者・メンテナ向けとして分離しています。

## ライセンス

リポジトリ内のコードについては [LICENSE](./LICENSE) を確認してください。元アプリ、パッチ、アイコンなどの権利はそれぞれの権利者に帰属します。
