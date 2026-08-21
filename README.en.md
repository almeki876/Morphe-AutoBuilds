# Morphe AutoBuilds

[Japanese](README.md) | [English]

[![Upstream Check](https://img.shields.io/github/actions/workflow/status/almeki876/Morphe-AutoBuilds/check-upstream.yml?label=upstream%20check)](https://github.com/almeki876/Morphe-AutoBuilds/actions/workflows/check-upstream.yml)
[![Build Status](https://img.shields.io/github/actions/workflow/status/almeki876/Morphe-AutoBuilds/build.yml?label=build)](https://github.com/almeki876/Morphe-AutoBuilds/actions/workflows/build.yml)
[![Latest Release](https://img.shields.io/github/v/release/almeki876/Morphe-AutoBuilds?label=latest%20release)](https://github.com/almeki876/Morphe-AutoBuilds/releases/latest)

Morphe AutoBuilds is a GitHub Actions based build repository that downloads Android base APKs, applies community Morphe/ReVanced-style patch bundles, signs the results, scans the unmodified inputs, and publishes installable APKs.

> [!IMPORTANT]
> This repository and its generated APKs are not official releases from the app vendors, Google, Morphe, ReVanced, or the patch authors. Check the base-APK source, VirusTotal result, and applied patches in each release before installing.

## Downloads

- [Latest release](https://github.com/almeki876/Morphe-AutoBuilds/releases/latest)
- [Obtainium / ObtainX install links](Morphe-AutoBuilds-Obtainium.md)

Builds normally prefer `arm64-v8a` and can fall back to `universal` when appropriate.

## What is built

[`my-patch-config.json`](my-patch-config.json) is the source of truth for enabled app/source combinations. Patch repositories and tool assets are declared in [`sources/`](sources/), while base-APK provider configuration lives under [`apps/`](apps/).

The README intentionally does not duplicate a manually maintained app list. This prevents documentation from drifting when configuration changes. See `my-patch-config.json` and release build results for the exact current matrix.

### Gboard

Gboard is a special integrated build that chains three patch sources into one APK:

1. `jason` (`jasonwu1994/Gboard-patches`) — compatibility/version authority
2. `adobo` (`jkennethcarino/adobo`)
3. `morning-entree` (`Entree3k/Morning-Entree-Patches`)

An update to any of the three selects the integrated Gboard build. Requested patches that the CLI silently drops are treated as a build problem rather than a successful partial application.

## Automation flow

`check-upstream.yml` runs daily at 09:00 UTC (about 18:00 JST) and checks every patch source declared in `sources/*.json` plus monitored base APKs. Changed source IDs are passed as `updated_sources`; changed apps are passed as `updated_apps`.

`build.yml` no longer needs a hand-maintained input for every patch source. It derives its matrix from the current configuration.

1. Download CLI and patch bundles declared by `sources/*.json`.
2. Build a matrix from changed sources/apps, or every enabled entry for a full test build.
3. Resolve and download a compatible base APK.
4. Validate package id, version name/version code, and ABI expectations.
5. Preserve the unmodified base APK for VirusTotal scanning.
6. Apply patches with Morphe CLI and verify the requested/applied patch result.
7. Sign the output APK.
8. Publish outputs whose base APK passed VirusTotal checks.
9. Advance `last-tags.json` only after the selected build, scan, and release complete successfully.

Per-app failures are recorded as build artifacts and can create/update GitHub Issues. A run may publish successful APKs as a Partial release when only part of the matrix fails.

## Base APK acquisition

For general Play-distributed apps, the downloader tries **Google Play first**, then validated fallback providers. The current code includes Google Play, JustAPK, apkeep, APKMirror, APKPure, Uptodown, Softonic, Aptoide, and APKCombo paths, while app-specific official GitHub releases or dedicated sources can override that general order.

Every candidate is checked before use by [`src/apk_validation.py`](src/apk_validation.py). A package/version mismatch is rejected and the next provider is tried. `universal` APKs are not incorrectly required to contain one exact ABI.

### Google Play authentication

The repository-local `gplaydl` supports, in order:

- `GPLAY_EMAIL` + `GPLAY_AAS_TOKEN`
- `GPLAY_EMAIL` + `GPLAY_AUTH_TOKEN`
- configured anonymous token dispensers

No third-party dispenser is hardcoded. Actions can use these repository variables/secrets:

| Name | Type | Purpose |
| --- | --- | --- |
| `GPLAY_DISPENSER_URLS` | Variable | Preferred comma/semicolon/newline-separated endpoint list |
| `GPLAY_DISPENSER_URL` | Variable | Single endpoint |
| `AURORA_DISPENSER_URL` | Variable | Legacy compatibility name |
| `GPLAYDL_API_KEY` | Secret | Optional `X-Api-Key` for a dispenser |
| `GPLAY_EMAIL` | Secret | Account email for direct authentication |
| `GPLAY_AAS_TOKEN` | Secret | AAS-token authentication |
| `GPLAY_AUTH_TOKEN` | Secret | Auth-token authentication |

An endpoint may be a base URL or include `/api/auth`. Multiple configured endpoints are attempted in order, and error handling avoids printing token/dispenser response bodies into CI logs.

## VirusTotal

VirusTotal scans the **unmodified base APK**, not the final patched APK. Existing SHA-256 results are queried first and only unknown hashes are uploaded. A malicious/suspicious detection, API failure, or analysis timeout prevents that APK from being published.

## Workflows

| Workflow | Purpose |
| --- | --- |
| `check-upstream.yml` | Check all declared patch sources and monitored APKs |
| `build.yml` | Download, validate, patch, sign, scan, release, and report failures |
| `test-build.yml` | Manually dispatch `build_all_sources=true` |
| `configuration-check.yml` | Validate config, compile Python, validate providers, run unit tests |
| `health-check.yml` | Repository/provider health checks |
| `pr-targeted-build-verification.yml` | Real targeted builds for selected PR changes |

Scheduled GitHub Actions can start later than the exact cron time when runners are busy.

## Operator setup

See [`SETUP.md`](SETUP.md) for repository secrets/variables, Google Play authentication, manual runs, and troubleshooting.

Important configuration files:

- `my-patch-config.json` — apps, patch sources, patch options
- `arch-config.json` — architecture overrides
- `sources/*.json` — CLI and patch-bundle repositories
- `apps/**.json` — package ids and base-APK provider settings
- `last-tags.json` — last **successfully published** upstream/APK state

`last-tags.json` is advanced only after a successful selected build and release, so temporary provider failures can be retried on the next upstream check.

## Notes

- Generated APKs are unofficial third-party builds.
- Review app terms, licenses, and applicable laws before use.
- The signing certificate differs from official app certificates, so an official installation may need to be removed first.
- A clean VirusTotal result is not a guarantee of safety.
- Google Play and third-party APK providers can change behavior or rate-limit automated access without notice.

## License / Credits

Rights to the apps, Morphe/ReVanced tooling, patch sets, and provider services remain with their respective owners. This repository is not an official project of those parties.
