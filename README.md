<div align="center">

# 🚀 Morphe AutoBuilds

[![Upstream Check](https://img.shields.io/github/actions/workflow/status/matchadaisuke/Morphe-AutoBuilds/check-upstream.yml?label=Upstream%20Check&style=for-the-badge&color=2ea44f)](https://github.com/matchadaisuke/Morphe-AutoBuilds/actions/workflows/check-upstream.yml)
[![Build Status](https://img.shields.io/github/actions/workflow/status/matchadaisuke/Morphe-AutoBuilds/build.yml?label=Build%20Status&style=for-the-badge&color=0366d6)](https://github.com/matchadaisuke/Morphe-AutoBuilds/actions/workflows/build.yml)
[![Latest Release](https://img.shields.io/github/v/release/matchadaisuke/Morphe-AutoBuilds?style=for-the-badge&label=Latest%20Release&color=orange)](https://github.com/matchadaisuke/Morphe-AutoBuilds/releases/latest)
[![Python Version](https://img.shields.io/badge/Python-3.11%2B-blue?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org/)

<p align="center">
  <strong>自動 Android APK ビルドシステム</strong><br>
  複数パッチソース対応 • GitHub Actions 自動実行 • JST タイムスタンプリリース
</p>

<p align="center">
  アップストリームのパッチリリースを自動検知し、ベース APK のダウンロード・パッチ適用・署名・リリース公開までを全自動で行うパイプラインです。
</p>

[![最新リリースを見る](https://img.shields.io/badge/最新リリースを見る-0A0A0A?style=flat&logo=github&logoColor=white)](https://github.com/matchadaisuke/Morphe-AutoBuilds/releases/latest)
[![バグ報告](https://img.shields.io/badge/バグ報告-0A0A0A?style=flat&logo=github&logoColor=white)](https://github.com/matchadaisuke/Morphe-AutoBuilds/issues)

</div>

---

## ⚡ ダウンロード

アップストリームが新バージョンをリリースすると自動的にビルドされます。タグは JST タイムスタンプ形式（例: `2026-04-21_15-30-JST`）です。

[**→ 最新リリースを見る**](https://github.com/matchadaisuke/Morphe-AutoBuilds/releases/latest)

| アプリ | パッチソース |
| :--- | :--- |
| **YouTube** | Morphe, Anddea |
| **YouTube Music** | Morphe, Anddea |
| **Instagram** | Piko |
| **AdGuard** | hoo-dles |
| **Prime Video** | hoo-dles |
| **Duolingo** | hoo-dles |
| **IBS Paint** | hoo-dles |
| **Icon Packer** | hoo-dles |
| **Nova Launcher** | hoo-dles |
| **Proton VPN** | hoo-dles |
| **Smart Launcher** | hoo-dles |
| **SoundCloud** | hoo-dles |
| **WPS Office** | hoo-dles |
| **Crunchyroll** | hoo-dles |
| **MEGA** | Tosox |
| **Proton Mail** | RookieEnough |
| **Disney+** | RookieEnough |
| **Photomath** | RookieEnough |
| **Pixiv** | RookieEnough |
| **ゆうちょ通帳アプリ** | YuzuMikan404 |
| **ゆうちょ認証アプリ** | YuzuMikan404 |

---

## 📱 ビルド対象

| アプリ | パッチソース | CLI |
| :--- | :--- | :--- |
| YouTube | Morphe | morphe-cli |
| YouTube | Anddea (revanced-anddea) | morphe-cli |
| YouTube Music | Morphe | morphe-cli |
| YouTube Music | Anddea (revanced-anddea) | morphe-cli |
| Instagram | Piko (crimera) | morphe-cli |
| AdGuard | hoo-dles | morphe-cli |
| Prime Video | hoo-dles | morphe-cli |
| Duolingo | hoo-dles | morphe-cli |
| IBS Paint | hoo-dles | morphe-cli |
| Icon Packer | hoo-dles | morphe-cli |
| Nova Launcher | hoo-dles | morphe-cli |
| Proton VPN | hoo-dles | morphe-cli |
| Smart Launcher | hoo-dles | morphe-cli |
| SoundCloud | hoo-dles | morphe-cli |
| WPS Office | hoo-dles | morphe-cli |
| Crunchyroll | hoo-dles | morphe-cli |
| MEGA | Tosox | revanced-cli v4.4.0 |
| Proton Mail | RookieEnough (De-ReVanced) | morphe-cli |
| Disney+ | RookieEnough (De-ReVanced) | morphe-cli |
| Photomath | RookieEnough (De-ReVanced) | morphe-cli |
| Pixiv | RookieEnough (De-ReVanced) | morphe-cli |
| ゆうちょ通帳アプリ | YuzuMikan404 | revanced-cli v5.0.1 |
| ゆうちょ認証アプリ | YuzuMikan404 | revanced-cli v5.0.1 |

---

## ✨ 主な機能

- **アップストリーム自動検知**: 全パッチソースの新リリースを毎日 JST 06:00 / 18:00 の2回監視し、更新があれば自動でビルドをトリガー
- **統合リリース**: 全 APK を JST タイムスタンプタグでまとめてリリース
- **複数 CLi 対応**: morphe-cli (v1.8.x / v1.9.x+) と revanced-cli (v4・v5+) を自動判別して適切な引数を構築
- **動的パッチ選択**: `patches-list.json` から対象アプリ・バージョンに互換性のあるパッチを自動選択
- **オプション制御**: `my-patch-config.json` でパッチオプション・無効化・強制有効化を一元管理
- **APK 自動取得**: APKMirror・APKPure・Uptodown・Aptoide から互換バージョンを自動検索してダウンロード
- **ツールキャッシュ**: ビルドツールをジョブ間でキャッシュし、ダウンロード時間を削減

---

## 📋 システム構成

### ワークフロー

```
check-upstream.yml（毎日 JST 06:00 / 18:00）
  └─ Morphe・Anddea の最新タグを取得
  └─ Repository Variables と比較
  └─ 差分があれば build.yml をトリガー

build.yml（アップストリーム更新時 or 手動実行）
  ├─ Prepare Build Matrix  … ビルド対象の組み合わせを生成
  ├─ Download Build Tools  … 各ソースのツールをダウンロード・キャッシュ
  ├─ Build APK（並列）     … 各 app × source の組み合わせをビルド
  └─ Create Integrated Release … 全 APK を統合してリリース作成
```

### リポジトリ構造

```
Morphe-AutoBuilds/
├── .github/workflows/
│   ├── check-upstream.yml      # アップストリーム監視
│   └── build.yml               # ビルド & リリース
├── src/                        # Python ビルドコア
│   ├── __main__.py             # エントリポイント・パッチ実行ロジック
│   ├── downloader.py           # APK・ツールダウンロード
│   ├── apkmirror.py            # APKMirror 対応
│   ├── apkpure.py              # APKPure 対応
│   ├── uptodown.py             # Uptodown 対応
│   ├── aptoide.py              # Aptoide 対応
│   ├── github.py               # GitHub Releases 対応
│   ├── utils.py                # 共通ユーティリティ
│   └── __init__.py             # セッション・ロギング初期化
├── scripts/
│   ├── prepare_matrix.py       # ビルドマトリクス生成
│   ├── download_all_tools.py   # ツール一括ダウンロード
│   ├── download_reused_apks.py # 再利用 APK のダウンロード
│   ├── check_apk_versions.py   # APK バージョン確認
│   └── save_apk_versions.py    # APK バージョン保存
├── apps/
│   ├── apkmirror/              # APKMirror からダウンロードするアプリの設定
│   ├── apkpure/                # APKPure からダウンロードするアプリの設定
│   ├── uptodown/               # Uptodown からダウンロードするアプリの設定
│   ├── aptoide/                # Aptoide からダウンロードするアプリの設定
│   └── github/                 # GitHub Releases からダウンロードするアプリの設定
├── sources/
│   ├── morphe.json             # Morphe ツール定義
│   ├── revanced-anddea.json    # Anddea ツール定義
│   ├── piko.json               # Piko ツール定義
│   ├── hoo.json                # hoo-dles ツール定義
│   ├── tosox.json              # Tosox ツール定義
│   ├── yuzu.json               # YuzuMikan404 ツール定義
│   └── rookie.json             # RookieEnough ツール定義
├── patches/
│   ├── youtube-morphe.txt
│   ├── youtube-revanced-anddea.txt
│   ├── youtube-music-morphe.txt
│   ├── youtube-music-revanced-anddea.txt
│   ├── yuucho-tsucho-yuzu.txt
│   └── yuucho-ninsho-yuzu.txt
├── keystore/                   # 署名用キーストア
├── my-patch-config.json        # パッチオプション・有効化・無効化の設定
├── arch-config.json            # アーキテクチャ設定
├── last-tags.json              # 前回リリースタグの記録
└── SETUP.md                    # セットアップガイド
```

---

## ⚙️ 設定ガイド

### 1. ビルド対象の定義（`my-patch-config.json`）

`patch_list` に app × source の組み合わせを記述します。

```json
{
  "patch_list": [
    {
      "app_name": "youtube",
      "source": "revanced-anddea",
      "options": [
        { "patch": "Custom branding name for YouTube", "key": "appName", "value": "RVA" },
        { "patch": "GmsCore support", "key": "packageNameYouTube", "value": "bill.youtube" }
      ],
      "disable": [],
      "force_enable": []
    }
  ]
}
```

**フィールド説明:**

| フィールド | 説明 |
| :--- | :--- |
| `app_name` | アプリ名（`youtube`、`youtube-music`、`instagram` 等） |
| `source` | パッチソース名（`sources/` 以下のファイル名と対応） |
| `options` | パッチオプションのリスト。`patch`・`key`・`value` を指定 |
| `disable` | 強制無効化するパッチ名のリスト |
| `force_enable` | デフォルト無効のパッチを強制有効化するリスト |

> **`force_enable` について**: `patches-list.json` 上で `use: false`（デフォルト無効）になっているパッチを有効化したい場合に使います。`patches/` の `.txt` ファイルの `+` 行は `patches-list.json` が存在する場合は参照されないため、このフィールドで指定してください。

### 2. アーキテクチャ設定（`arch-config.json`）

```json
[
  { "app_name": "youtube",       "source": "morphe",          "arches": ["arm64-v8a"] },
  { "app_name": "youtube-music", "source": "revanced-anddea", "arches": ["arm64-v8a"] }
]
```

### 3. パッチフィルター（`patches/<app>-<source>.txt`）

`patches-list.json` が存在しないソースや、フォールバック時に参照されます。

```text
# - で始まる行 = 無効化
# + で始まる行 = 強制有効化（patches-list.json がない場合のみ有効）

- Custom branding name for YouTube
+ Return YouTube Dislike
```

> `patches-list.json` があるソース（morphe、revanced-anddea 等）では動的選択が優先されます。無効化は `my-patch-config.json` の `disable`、強制有効化は `force_enable` で設定してください。

### 4. APK ダウンロード設定（`apps/<platform>/<app>.json`）

各プラットフォームからのダウンロード設定です。APKMirror の例:

```json
{
  "org": "google-inc",
  "name": "youtube",
  "type": "APK",
  "arch": "universal",
  "dpi": "nodpi",
  "package": "com.google.android.youtube",
  "version": ""
}
```

`version` を空にすると、パッチと互換性のある最新バージョンが自動選択されます。

### 5. パッチソース定義（`sources/<source>.json`）

ツールのダウンロード元（GitHub リリース）を定義します。

```json
[
  { "name": "revanced-anddea" },
  { "user": "MorpheApp", "repo": "morphe-cli",       "tag": "latest" },
  { "user": "anddea",    "repo": "revanced-patches",  "tag": "latest" }
]
```

---

## 🚀 セットアップ

詳細は [SETUP.md](./SETUP.md) を参照してください。

```bash
# Repository Variables を初期化
morphe_tag=$(gh api repos/MorpheApp/morphe-patches/releases/latest --jq '.tag_name')
gh variable set LAST_MORPHE_TAG --body "$morphe_tag"

anddea_tag=$(gh api repos/anddea/revanced-patches/releases/latest --jq '.tag_name')
gh variable set LAST_ANDDEA_TAG --body "$anddea_tag"

# 手動でビルドを実行
gh workflow run build.yml

# アップストリームチェックを手動実行
gh workflow run check-upstream.yml
```

---

## 📥 インストール方法

1. 最新リリースから目的の APK をダウンロード
2. Android の「提供元不明のアプリ」を許可
3. 既存の YouTube / YouTube Music をアンインストール
4. APK をインストール
5. 必要に応じて [MicroG-RE](https://github.com/ReVanced/GmsCore) をインストール（Google アカウント連携に必要）

---

## 🔗 パッチソース一覧

| ソース名 | リポジトリ |
| :--- | :--- |
| Morphe | https://github.com/MorpheApp/morphe-patches |
| Anddea | https://github.com/anddea/revanced-patches |
| Piko | https://github.com/crimera/piko |
| hoo-dles | https://github.com/hoo-dles/morphe-patches |
| Tosox | https://github.com/Tosox/revanced-patches |
| YuzuMikan404 | https://github.com/YuzuMikan404/linegms-fork-second- |
| RookieEnough | https://github.com/RookieEnough/De-ReVanced |

---

## ⚠️ 免責事項

- Morphe・各パッチ作者の公式ツールを使用した自動ビルドです
- Morphe・Anddea・その他パッチ作者との公式な提携関係はありません
- 自己責任でご利用ください
- Google アカウント連携には MicroG-RE が必要な場合があります

---

<div align="center">

**役に立ったと思ったら ⭐ Star をお願いします**

</div>
