# Morphe AutoBuilds: Setup and Operations

English | [日本語](SETUP.ja.md)

This guide is for people who want to fork, change, or maintain this repository. If you only want to install an APK, start with the [user guide](README.md).

## What this repository does

The GitHub Actions workflows select apps and patch sources, obtain a compatible original APK, apply community patches, verify the result, and publish successful APKs as GitHub Releases. A run can publish the apps that succeeded even if another app failed.

The main files and folders are:

| Path | Purpose |
| --- | --- |
| `my-patch-config.json` | Apps and patch sources to build |
| `arch-config.json` | APK architectures to produce |
| `app-metadata/` | Package IDs and download rules shared by providers |
| `apps/<provider>/` | Settings for individual APK providers |
| `sources/` | Patch CLI and patch-bundle sources |
| `src/` | Shared provider, version, download, patch, and build code |
| `scripts/` | Workflow commands and tools for manual diagnosis |
| `.github/workflows/` | When workflows run and how jobs are connected |
| `tests/` | Regression tests |

Put complex decisions and network operations in `src/` or `scripts/`, not in long YAML blocks. Workflows should mainly describe when to run a task and which script to call.

Workflows that need Python use the shared `.github/actions/setup-python-project` composite action. It selects Python 3.11 and can install `requirements.txt` when the job needs project dependencies; set `install-dependencies: 'false'` for standard-library-only tasks. This keeps Python versions and dependency setup consistent without installing packages in every job.

## GitHub Actions: which workflow should I run?

The Actions page keeps the main build, a one-click full build, validation, and maintenance tasks separate:

| Workflow | When to use it |
| --- | --- |
| `Build and Release APKs` (`build.yml`) | Builds and releases selected apps, usually started automatically when updates are found. |
| `Build All Apps` (`build-all-apps.yml`) | One-click manual entry point that starts `build.yml` for every enabled app/source. |
| `Repository Validation` (`configuration-check.yml`) | Runs automatically for pushes to the main branches and pull requests. |
| `Repository Operations` (`operations.yml`) | Runs scheduled maintenance or a selected manual maintenance task. |

Use `Build All Apps` when you want to rebuild everything; it does not share the maintenance task selector.

Select **Repository Operations → Run workflow** and choose one task from the `task` menu:

| Task | Purpose |
| --- | --- |
| `check-upstream` | Checks patch and APK updates; starts a build when changes are found. Runs twice daily automatically. |
| `provider-health` | Checks APK providers and build tools. Runs daily automatically. |
| `refresh-download-links` | Regenerates the English and Japanese APK catalogs. Runs daily automatically; successful build-state updates also refresh both catalogs. |
| `diagnose-google-play` | Diagnoses Google Play downloads for the supplied packages and optional version code. |
| `verify-japan-egress` | Checks the Tailscale connection and confirms the outbound IP is in Japan. |
| `rebuild-release-details` | Regenerates details for an existing Release using artifacts from a build run. Requires `release_tag` and `run_id`. |
| `register-google-play` | Registers the Google account used by gplaydl. Requires `expected_email`. |

Keep the full-build entry point separate from maintenance operations. Routine checks run on a schedule; use manual diagnostic or account-registration tasks only when needed.

## Before changing a workflow

`build.yml` coordinates the build pipeline. Keep download, version resolution, VirusTotal, and state-saving logic in Python modules or scripts when possible.

- Do not duplicate the same operation across multiple workflows.
- Avoid putting long Python programs directly in workflow YAML.
- Give credentials only to the steps that need them.
- Use `continue-on-error` only when a later step explicitly checks the result.
- Give fallback paths a timeout and a clear stopping condition.
- If a workflow's displayed name changes, update any `workflow_run.workflows` references.
- If a workflow filename changes, check references in badges, `gh workflow run` commands, tests, and documentation.

## Required GitHub Secrets and Variables

Add these under the repository's **Settings → Secrets and variables → Actions**. The workflows that use each value will fail or skip their related feature if it is missing.

### Repository secrets

| Secret | Purpose |
| --- | --- |
| `PAT` | Reads private patch sources and updates secrets during Google Play account registration |
| `VIRUSTOTAL_API_KEY` | Scans the original, unpatched APK |
| `GPLAYDL_API_KEY` | Downloads apps from Google Play with gplaydl |
| `GPLAY_EMAIL` | Email address of the registered Google Play account |
| `GPLAY_AAS_TOKEN` | AAS token obtained through the gplaydl Authenticator |
| `TS_OAUTH_CLIENT_ID` | Connects GitHub Actions to Tailscale |
| `TS_OAUTH_SECRET` | Connects GitHub Actions to Tailscale |

### Repository variables

| Variable | Purpose |
| --- | --- |
| `TS_EXIT_NODE` | Tailscale exit node in Japan |
| `GPLAYDL_PREFERRED_PROFILE` | Preferred Google Play device profile; auto-selected if unset |

Never put secret values in logs, issues, or commits. If a secret is exposed, replace it immediately.

## Google Play download flow

Before requesting an exact Google Play version, the build resolves and checks the Android `versionCode` that matches the `versionName` accepted by the patch CLI.

`src/providers.py` can fill in a `versionCode` for the same `versionName`; it does not change the version that the patches support. If the matching code cannot be confirmed, the build does not guess a nearby version.

`src/aurora_play.py` handles Google Play downloads. When the version code is known, it passes `-v <versionCode>` to gplaydl to request that exact version. The usual fallback order is:

1. gplaydl
2. playfetch
3. apkeep
4. gplaydl with a fresh device profile, when needed

Each command has a timeout, and the whole Google Play attempt has a time budget. A failed download path should not wait indefinitely.

## APK download fallback

The build tries these routes in order:

1. Google Play from the normal GitHub Actions runner.
2. If that fails, connect to the Tailscale exit node in Japan, confirm the Japan egress IP, and try Google Play again.
3. If Google Play still fails, try the configured APK providers and mirrors from the normal runner.
4. If the normal runner is blocked by a CDN or similar service, retry the provider chain once through Tailscale in Japan.

The final provider retry skips Google Play. This prevents a retry loop and avoids reconnecting to Google Play unnecessarily.

HTTP providers download to a temporary `.part` file. The build checks the content length, APK/ZIP format, and package/version identity before accepting the file. An interrupted download or an HTML error page must not be treated as an APK.

## Apps restricted to Japan

Some apps, such as ゆうちょ通帳 and ゆうちょ認証, have regional availability restrictions in Google Play. Keep the Tailscale exit node in Japan available as a download route.

The Google account used for builds must also be able to access the app in the Japanese Google Play store. After connecting to Tailscale, `scripts/verify_japan_egress.py` checks that the public IP is in Japan before the workflow uses account credentials.

## VirusTotal checks

Before release, the workflow checks the SHA-256 hash of each original, unpatched base APK:

1. Look for the hash in the persistent cache.
2. If it is not cached, ask VirusTotal whether the file is already known.
3. Upload only hashes that VirusTotal does not know.
4. Poll newly started analyses together.
5. Save successful results to the cache.

Google Play split APKs are combined deterministically into an `.apks` archive, so the same APK set produces the same hash even if runner or file timestamps differ. Successful VirusTotal results are also saved as a Release asset so they can be reused if the Actions cache is lost. If the scan cannot be completed, the workflow blocks the release.

## Registering a Google Play account

1. Add `GPLAYDL_API_KEY` as a repository secret.
2. In `Repository Operations`, select `register-google-play`.
3. Enter the account email in `expected_email`.
4. Follow the temporary Authenticator URL shown in the job summary using the official gplaydl Authenticator.
5. Add the requested account.
6. Confirm that the workflow saved `GPLAY_EMAIL` and `GPLAY_AAS_TOKEN` as repository secrets.

Do not continue using the temporary URL after registration.

## First-time setup

1. Enable GitHub Actions for the repository.
2. Configure the secrets and variables listed above.
3. Run `python3 scripts/validate_repository.py` and resolve any reported errors.
4. Check the `Repository Validation` workflow.
5. In `Repository Operations`, run `verify-japan-egress` to verify the Tailscale Japan route.
6. Register a Google Play account if the selected apps require it.
7. Run `Build All Apps` and check the Release and job summaries.

## Local checks

Before opening a pull request, run:

```bash
pip install -r requirements.txt
python3 scripts/validate_repository.py
python3 -m compileall -q src scripts
python3 -m unittest discover tests
```

The `Repository Validation` workflow runs the corresponding configuration, syntax, and unit-test checks on GitHub Actions.

## Diagnosing an APK provider

Use `scripts/probe_apk_sources.py` to inspect provider URL resolution or CDN responses. It is a maintenance tool, not a step in every build.

```bash
python3 scripts/probe_apk_sources.py \
  --app nova \
  --version 8.8.6 \
  --code 88600
```

Without `--download-dir`, the script checks only the first bytes from the resolved URL and verifies that the response looks like an APK archive. Use it when investigating a provider change.

## Rules for configuration changes

- Keep package IDs consistent across providers for the same app.
- Do not replace the version accepted by the patch CLI with another version.
- Do not pin `version_code` in a configuration file.
- Do not report a failed download as successful by substituting a newer version.
- Do not cache provider HTML pages or invalid APK files.
- Never print Google Play, Tailscale, or VirusTotal secrets in logs.
- Move complex workflow decisions into a script or module rather than adding long YAML logic.

## Scheduled updates and saved state

The `check-upstream` task in `Repository Operations` checks patch sources and APK updates twice a day, at 03:00 and 21:00 Japan time. It dispatches `build.yml` only for changed targets. GitHub Actions schedules use UTC, so the workflow uses `18:00 UTC` on the previous day and `12:00 UTC`.

Successful build state is saved in `last-tags.json`. If another workflow pushes to `main` at the same time, the state-saving script reapplies only this run's changes to the latest `main` before pushing. It does not force-push.

## Releases and partial failures

A release is created only when:

- At least one APK in the build scope completed.
- The VirusTotal scan for the original APK succeeded.
- Package and version identity checks passed.

If some apps fail, the release may contain only the APKs that completed successfully. Per-app status artifacts and the issue reporter contain failure details.

## Maintenance tasks

### `diagnose-google-play`

Use this to investigate why Google Play cannot provide a specific package and `versionCode`. It does not replace a normal build.

### `verify-japan-egress`

Use this to check the Tailscale connection, selected exit node, and Japan IP independently. It helps distinguish a Google Play issue from a Tailscale issue.

### `provider-health`

This periodically probes build tools and APK providers. It is intended to identify wider provider problems, not to diagnose a one-off failure in a normal build.

## Before removing a file

Do not delete a file just because its name looks old. Check whether it is referenced by:

1. A workflow.
2. A Python import.
3. Tests.
4. The README, this guide, or generated documentation.
5. A manual diagnostic procedure.

If a file is intentionally kept for migration or diagnosis despite having no direct references, document its purpose here.

## User documentation

Keep download instructions, architecture choices, safety information, and problem-reporting guidance in the user-facing [README](README.md). Keep implementation and operations details in this guide.
