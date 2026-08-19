# Morphe AutoBuilds

[Japanese](README.md) | [English]

[![Upstream Check](https://img.shields.io/github/actions/workflow/status/matchadaisuke/Morphe-AutoBuilds/check-upstream.yml?label=upstream%20check)](https://github.com/matchadaisuke/Morphe-AutoBuilds/actions/workflows/check-upstream.yml)
[![Build Status](https://img.shields.io/github/actions/workflow/status/matchadaisuke/Morphe-AutoBuilds/build.yml?label=build)](https://github.com/matchadaisuke/Morphe-AutoBuilds/actions/workflows/build.yml)
[![Latest Release](https://img.shields.io/github/v/release/matchadaisuke/Morphe-AutoBuilds?label=latest%20release)](https://github.com/matchadaisuke/Morphe-AutoBuilds/releases/latest)

Morphe AutoBuilds is an automated repository that applies community-made patches to Android applications and builds installable APK files. Patches are differential modifications designed to alter features, visual elements, or behaviors of the base app.

GitHub Actions automatically monitors updates for patch sources and target applications, downloads compatible base APKs, applies patches, signs the output APKs, scans them via VirusTotal, and publishes the resulting releases on GitHub Releases. When different patch sources target the same app, separate APKs are created for each source.

> [!IMPORTANT]
> This repository and its distributed APKs are NOT official releases from original application developers, Google, ReVanced, Morphe, or patch authors. Please read the "Safety and Precautions" section before use.

## Downloading APKs

Built APKs are available for download on the [Latest Release](https://github.com/matchadaisuke/Morphe-AutoBuilds/releases/latest) page. Release titles include the creation date and time in JST.

Each release note includes:

- Successfully built and failed applications
- Used patch sources and versions
- Base APK version, architecture, and provider source
- VirusTotal inspection status and SHA-256 file hashes

APK filenames contain the app name, target architecture, and base APK version. `arm64-v8a` builds are prioritized, automatically falling back to `universal` builds if `arm64-v8a` is unavailable.

## Supported Applications

The current configuration builds the following combinations:

| Patch Source | Target Applications |
| --- | --- |
| [Morphe (Official)](https://github.com/MorpheApp/morphe-patches) | YouTube, YouTube Music |
| [Anddea](https://github.com/anddea/revanced-patches) | YouTube, YouTube Music |
| [rushiranpise](https://github.com/rushiranpise/morphe-patches) | 1.1.1.1, AccuBattery, AdGuard, Adobe Scan, Amazon Shopping, Call Recorder, CamScanner, Countdown Widget, Excel, File Manager, Kahoot, KineMaster, MEGA, Ninja VPN, SD Maid SE, Speedtest, Windscribe VPN, Word, Yuucho Tuucho, Yuucho Ninsho |
| [Hoo-dles](https://github.com/hoo-dles/morphe-patches) | Lightroom Mobile |
| [shaun-the-sheep-patches](https://github.com/shaun-the-sheep-patches/morphe-patches) | Kinestop |
| [RookieEnough](https://github.com/RookieEnough/De-Vanced) | Amazon Music, Disney+, Google News, Google Photos, Google Recorder, Photomath, Adobe Photoshop Mix, Pixiv, Tumblr, Viber |
| [ajstrick81](https://github.com/ajstrick81/morphe-androidtv-patches) | Disney+ (Android TV), Netflix, Prime Video (Android TV), Twitch (Android TV) |
| [andrewliang25](https://github.com/andrewliang25/morphe-patches) | LINE |
| [Hoomans](https://github.com/arandomhooman/hoomans-morphe-patches) | Adobe Acrobat, FolderSync, InShot, Poweramp, Twitch |
| [hxreborn](https://github.com/hxreborn/morphe-patches) | Proton Mail |
| [icysymmetra](https://github.com/icysymmetra/tiktok-patches-for-morphe) | TikTok |
| [durgesh0505](https://github.com/durgesh0505/chiggi_morphe_patches) | Threads |
| [Morning-Entree](https://github.com/Entree3k/Morning-Entree-Patches) | Gboard, Nova Launcher, Sleep as Android |
| [Jason (jasonwu1994)](https://github.com/jasonwu1994/Gboard-patches) | Gboard |
| [Adobo (jkennethcarino)](https://github.com/jkennethcarino/adobo) | Gboard |
| [Paresh](https://github.com/Paresh-Maheshwari/paresh-patches) | Fing, Proton VPN |
| [dh6k](https://github.com/dh6k/morphe-patches) | Brave, Brave Beta, Brave Nightly |
| [BholeyKaBhakt](https://github.com/BholeyKaBhakt/android-patches-xtra) | Speedtest |
| [Fluffy (rabilrbl)](https://github.com/rabilrbl/fluffy-patches) | Alarmy |
| [Quantro](https://github.com/Quantro100/Morphe-patches) | AliExpress |
| [Lain (kiraio-moe)](https://github.com/kiraio-moe/Lain-Patches) | iLovePDF |

Targets and applied patches may change over time according to upstream updates. Please inspect individual release notes and GitHub Actions logs for exact details of applied patches.

## Automated Build Workflow

This repository checks for updates to registered patch sources and target APKs daily around 18:00 (JST). Scheduled execution on GitHub Actions may be delayed depending on GitHub queue load.

A separate periodic health check workflow validates repository configuration, patch tool release assets, and alternative APK providers every day. If all providers for an app fail or a tool fetch error occurs, a diagnostic report is uploaded, and an issue is automatically opened or updated. Once restored, the issue is closed automatically.

A configuration check workflow runs on every push and pull request, validating JSON syntax, package IDs, source definitions, architecture settings, Python syntax, and provider registrations.

When updates are detected, the workflow performs:

1. Resolving versions for patch tools and patch bundles
2. Identifying compatible base APK versions for the patches
3. Downloading base APKs from multiple providers
4. Validating APK structure and download contents
5. Saving raw base APKs for security scanning
6. Applying patches and signing output APKs with repository keystore
7. Scanning raw base APKs on VirusTotal
8. Publishing GitHub Releases with provenance metadata if VirusTotal check succeeds

If only certain apps fail to build, partial releases containing only successful APKs may be published. If zero APKs are built or VirusTotal inspection fails, no release is published.

A test workflow (`test-build.yml`) is also available for forcing all sources to build. When triggered manually, it fetches the latest tag for each source and runs the build with `force_build` flags set for every source.

## Base APK Retrieval Strategy

Compatible versions are searched across the following providers in priority order:

1. APKMirror
2. APKPure
3. Uptodown
4. Softonic
5. Aptoide
6. APKCombo

Even without app-specific settings, providers can be searched using package IDs. Network timeouts, rate limits, incomplete downloads, HTML challenge pages, or corrupted APK files automatically trigger fallback to the next provider.

When patch tools return both version code and version name (e.g. `88600 (8.8.6)`), both identifiers are retained to match provider-specific APIs. For APKPure, direct download endpoints (`d.apkpure.net`) are preferred if standard Web pages encounter Cloudflare challenges. Interactive CAPTCHA/bot challenges trigger immediate fallback to alternative providers.

On APKMirror, compatible older versions are verified using publisher/app slugs. Latest release monitoring targets dedicated category URLs (`/uploads/?appcategory=<name>`), parsing release links only for the target app to avoid unrelated versions. Version codes discovered from APKMirror variant tables are passed to APKPure fallbacks, preserving version accuracy.

AdGuard is downloaded exclusively from official GitHub Releases ([AdguardTeam/AdguardForAndroid](https://github.com/AdguardTeam/AdguardForAndroid)). Third-party mirrors, pre-releases, and TV variants are excluded. If unavailable from GitHub, build stops rather than retrieving from unverified mirrors.

Japan Post Bank apps prioritize release assets from [YuzuMikan404/Yuucho-Tuucho-and-Ninsho](https://github.com/YuzuMikan404/Yuucho-Tuucho-and-Ninsho), falling back to standard providers only when missing.

Downloaded raw APKs are stored in an internal cache with SHA-256 validation, reused only when package ID and version match exactly.

## VirusTotal Inspection

Raw base APKs saved immediately after download are inspected on VirusTotal. Patched and signed output APKs are not uploaded to minimize API consumption and processing time.

Files are checked by SHA-256 hash first. If existing scan results exist on VirusTotal, they are reused without uploading. New files are uploaded for analysis. Release is aborted if:

- Raw base APK receives 1 or more `malicious` or `suspicious` flags
- API errors or quota limits prevent analysis completion
- Analysis times out
- Inspection engine results cannot be fetched

Scan results are attached to release notes and saved as GitHub Actions summary reports. If detections occur, engine details, detection names, categories, and definitions are logged to Actions markdown and full JSON artifacts. Note that clean VirusTotal results do not guarantee absolute security.

## Installation Basics

1. Open [Latest Release](https://github.com/matchadaisuke/Morphe-AutoBuilds/releases/latest) and select the APK for your target app and patch source.
2. Check release notes for base APK provider, version, and VirusTotal results.
3. Backup app data if necessary.
4. Open APK on your Android device and follow on-screen prompts to install.

Android may require enabling "Install unknown apps" permission for your browser or file manager. Reverting temporary permissions after installation is recommended.

## Usage Precautions

- Distributed APKs use different signatures than official Play Store apps and cannot be installed as direct updates over official apps.
- If uninstalling existing official apps, back up app settings and data first.
- Google account login on YouTube apps may require separate GmsCore components.
- Using patched applications may violate terms of service for target platforms.
- Modifying financial or authentication apps carries inherent security risks. Use official apps if you cannot independently verify safety.

## Repository Administration

Regular users do not need setup. Administrators running custom automated builds or forks should refer to [SETUP.md](./SETUP.md).

Main configurations:

| File / Directory | Purpose |
| --- | --- |
| `my-patch-config.json` | Apps to build, patch sources, and patch options |
| `arch-config.json` | Per-app target architecture rules |
| `apps/` | Package IDs and provider configurations |
| `sources/` | Patch tool and bundle source rules |
| `scripts/probe_apk_sources.py` | Diagnostic script for provider testing |
| `scripts/validate_repository.py` | JSON schema, package ID, and architecture consistency validator |
| `scripts/provider_health.py` | Daily provider health check |
| `scripts/detect_version_pinned.py` | Detects version-pinned apps from patch bundles |
| `scripts/check_apk_versions.py` | Detects APK updates for version-pinned apps |
| `scripts/release_metadata.py` | Generates build result metadata (success/failure counts, etc.) |
| `scripts/release_notes.py` | Generates release notes in Markdown |

| Workflow | Purpose |
| --- | --- |
| `check-upstream.yml` | Patch source and APK update detection, build triggering |
| `build.yml` | Tool download, matrix build, VirusTotal scan, release publishing |
| `health-check.yml` | Configuration validation, tool release verification, daily APK provider health check |
| `configuration-check.yml` | Push/PR configuration consistency check and Python compile check |
| `test-build.yml` | Force build all sources for testing (manual trigger) |

## Disclaimer

This repository is an unofficial project and is not affiliated with target app developers, Google, ReVanced, or Morphe projects. End users bear full responsibility for using distributed artifacts and managing their accounts, devices, and service agreements.

To report issues, submit a report via [Issues](https://github.com/matchadaisuke/Morphe-AutoBuilds/issues) with execution time, target app name, and failed build steps (excluding sensitive information).
