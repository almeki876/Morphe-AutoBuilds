# Gboard — ビルド詳細

- **Status:** `success_full`
- **Version:** `18.0.3.954559732-release-arm64-v8a`
- **Patch source:** Jason + Adobo + Morning-Entree (3-source integrated)
- **Applying count:** `37`
- **Applied count:** `37`
- **Required satisfied:** `True`

## Gboard 3-source integration

1つのAPKに **Jason + Adobo + Morning-Entree** の3ソースを統合しています。
補助2ソースは明示allowlistだけを有効化し、Jasonと重複する機能はJason側を優先して抑止します。
Release details公開前に、補助ソースの有効パッチが実際に `Applied:` と報告されたことも検証します。

[GitHub Actions run を開く](https://github.com/almeki876/Morphe-AutoBuilds/actions/runs/32724893960)
