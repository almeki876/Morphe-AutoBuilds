# Morphe AutoBuilds

[Japanese](README.md) | [English]

[![Build Status](https://img.shields.io/github/actions/workflow/status/almeki876/Morphe-AutoBuilds/build.yml?label=build)](https://github.com/almeki876/Morphe-AutoBuilds/actions/workflows/build.yml)
[![Latest Release](https://img.shields.io/github/v/release/almeki876/Morphe-AutoBuilds?label=latest%20release)](https://github.com/almeki876/Morphe-AutoBuilds/releases/latest)

Morphe AutoBuilds automatically builds Android APKs with community-made patches and publishes the results through GitHub Releases.

> [!IMPORTANT]
> This repository and its APKs are not official releases from the original app developers, Google, ReVanced, Morphe, or individual patch authors. Use them at your own discretion.

## Download APKs

Choose the option that best fits how you update apps:

- **[Latest Release](https://github.com/almeki876/Morphe-AutoBuilds/releases/latest)** — APKs produced by the latest release run
- **[Direct APK links](./Morphe-AutoBuilds-Direct-Download.md)** — current APKs grouped by patch source, app, and architecture
- **[Obtainium / ObtainX links](./Morphe-AutoBuilds-Obtainium.md)** — links for registering supported app/source combinations in an update manager

The direct-download list is the simplest place to see what is currently available.

## Which architecture should I use?

- `arm64-v8a` — most modern Android phones and tablets
- `armeabi-v7a` — some older 32-bit ARM devices
- `x86_64` / `x86` — compatible emulators or devices
- `universal` — includes support for multiple architectures

If you are unsure, use `arm64-v8a` when available, otherwise use `universal`.

## Patch sources

The same app may be available from more than one patch source, such as Morphe, Anddea, or rushiranpise. Features and behavior can differ between sources.

See the [Direct APK links](./Morphe-AutoBuilds-Direct-Download.md) for the combinations that are currently distributed.

## Safety

Before release, the project checks the **unmodified base APK** with VirusTotal. A build is not treated as a valid release if the required scan cannot be completed.

VirusTotal and automated validation cannot guarantee absolute safety. Review release information and perform your own verification when appropriate.

Patched APKs are signed differently from official releases. As a result, an APK from this repository may not install directly over an existing copy signed by the original developer or another distributor.

## Updates

Patch sources and target apps are checked regularly. Only affected combinations are rebuilt when possible.

If some apps fail while others complete successfully, the successfully built and verified APKs may still be published. Per-app failures are reported through GitHub Actions and Issues.

## Report a problem

Open **Issues → New issue** and choose the form that matches the problem: build/download, installation/runtime, Actions/Release, another problem, or a feature/app request.

Include the affected app, patch source, release, and what happened when you know them.

## Development and self-hosting

Repository setup, Secrets, Google Play, Tailscale, VirusTotal, provider configuration, workflow operation, and maintainer guidance are documented in **[SETUP.md](./SETUP.md)**.

README files are intentionally user-facing; implementation and operations documentation belongs in SETUP.

## License

See [LICENSE](./LICENSE) for repository code licensing. Rights to original apps, patches, icons, and other third-party assets remain with their respective owners.
