# ibis Paint X — APK取得元

- **Version:** `14.0.6`
- **Package:** `jp.ne.ibis.ibispaintx.app`
- **Architecture:** `universal`
- **取得経路:** GitHub Base APK Cache から復元
- **Cache tag:** `base-apk-cache-v2`
- **元Provider:** `不明（origin sidecar導入前のlegacy cache）`
- **取得時の確認:** package/version identity 検証済み

> このReleaseの実Actionsログでは、`14.0.6` のBase APKをGitHub Base APK Cacheから復元したことまで確認できます。ただし、このcache assetはorigin sidecar導入前に作成されたため、元々Google Play / APKMirror / Uptodown等のどこから取得したAPKだったかを後から正確に復元することはできません。
>
> 今後新規に取得・更新されるcacheでは、ダウンロード時に元Provider・元URL・package・version・architecture・SHA-256を`origin.json`へ記録し、そのsidecarをcache assetと一緒に保存します。将来のcache復元時は、このページで「Cacheから復元」だけでなく元Providerと元URLまで表示されます。
