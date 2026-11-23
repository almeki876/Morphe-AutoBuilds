# claude — 適用パッチ

この一覧は設定値ではなく、ビルド時にCLIが `Applied:` と報告した結果を基準にしています。

**適用パッチ数:** 1

| Patch | Patch source | Source tag | Description |
| --- | --- | --- | --- |
| `Block Sentry telemetry` | [proteus](https://github.com/ineednotitle/Proteus-Patches) | `v2.4.0` | Upstream metadataに説明がありません。 |

## 未適用・除外

- `GmsCore support` — [runtime-skipped] MicroG RE
- `Disable PairIP license check` — [runtime-skipped-default] default
- `Provide Original app certificate` — [runtime-skipped-default] default
- `Spoof Widevine / DRM level` — [runtime-skipped-default] default
- `Fix Firebase after re-signing` — [runtime-skipped-default] default
- `Block cleartext` — [runtime-skipped] HTTP
- `Block user-added certificate authorities` — [runtime-skipped-default] default
- `Disable app backup` — [runtime-skipped] allowBackup=false
- `Remove dangerous permissions` — [runtime-skipped-default] default
- `Disable video autoplay` — [runtime-skipped] ExoPlayer/Media3
- `Block in-app update & review prompts` — [runtime-skipped-default] default
- `Block Play Billing purchase prompts` — [runtime-skipped-default] default
- `Prefer external browser over Custom Tabs` — [runtime-skipped-default] default
- `Force offline mode` — [runtime-skipped] simulate no network
- `Strip logging` — [runtime-skipped] android.util.Log
- `Allow screenshots & screen recording` — [runtime-skipped-default] default
- `Block advertising & device IDs` — [runtime-skipped-default] default
- `Block analytics & tracking SDKs` — [runtime-skipped-default] default
- `Block crash-reporting SDKs` — [runtime-skipped-default] default
- `Block screenshots & screen recording` — [runtime-skipped-default] default
- `Empty telephony identifiers` — [runtime-skipped] IMEI, phone number, SIM
- `Hide developer options & USB debugging` — [runtime-skipped-default] default
- `WebView hardening` — [runtime-skipped-default] default
