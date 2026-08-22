"""Central registry for APK download providers.

Provider order, display metadata, module bindings, and provider-specific
request settings live here so adding or reordering a provider is a one-file
change.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from types import ModuleType

from src import apkcombo, apkmirror_latest, apkpure, aptoide, github, softonic
from src import uptodown_exact as uptodown
from src.versioning import VersionCandidate, pinned_candidate


DOWNLOAD_PRIORITY = (
    "apkmirror",
    "apkpure",
    "uptodown",
    "softonic",
    "aptoide",
    "apkcombo",
)

# Prefer package-addressed machine-readable metadata for patch identity
# enrichment. More resolvers can opt in by exposing resolve_candidate_identities.
IDENTITY_RESOLUTION_PRIORITY = (
    "uptodown",
    "apkmirror",
)

PRIMARY_PROVIDER_KEY = "primary"

MODULES: dict[str, ModuleType] = {
    "github": github,
    "apkmirror": apkmirror_latest,
    "apkpure": apkpure,
    "uptodown": uptodown,
    "softonic": softonic,
    "aptoide": aptoide,
    "apkcombo": apkcombo,
}

DETAILS = {
    "apkmirror": ("APKMirror", "https://www.apkmirror.com/"),
    "apkpure": ("APKPure", "https://apkpure.com/"),
    "uptodown": ("Uptodown", "https://en.uptodown.com/android"),
    "softonic": ("Softonic", "https://en.softonic.com/android"),
    "aptoide": ("Aptoide", "https://en.aptoide.com/"),
    "apkcombo": ("APKCombo", "https://apkcombo.com/"),
    "github": ("GitHub", "https://github.com/"),
    "cache": ("GitHub Base APK Cache", ""),
}

# These providers can search from a package ID without a hand-maintained slug.
AUTO_CONFIG_PROVIDERS = frozenset(
    {"apkmirror", "apkpure", "aptoide", "apkcombo"}
)

# Search existing app configs in deterministic order when deriving a package ID.
CONFIG_SOURCE_PRIORITY = (
    "apkmirror",
    "apkpure",
    "uptodown",
    "aptoide",
    "github",
)


def load_config(
    app_name: str,
    provider: str,
    *,
    allow_synthetic: bool = True,
) -> dict | None:
    """Load one provider config or derive a package-only fallback."""
    config_path = Path("apps") / provider / f"{app_name}.json"
    if config_path.is_file():
        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError(f"invalid provider config {config_path}: {error}") from error
        if not isinstance(config, dict):
            raise ValueError(f"provider config must be an object: {config_path}")
        if not config.get("package"):
            raise ValueError(f"provider config has no package ID: {config_path}")
        return config

    if not allow_synthetic or provider not in AUTO_CONFIG_PROVIDERS:
        return None
    package = configured_package(app_name)
    if not package:
        return None
    logging.info(
        "🛟 %s: derived fallback config for %s from package %s",
        provider,
        app_name,
        package,
    )
    return {
        "name": app_name.replace("_", "-"),
        "package": package,
        "version": "",
    }


def configured_package(app_name: str) -> str | None:
    """Return one consistently configured package ID for an app."""
    found: list[tuple[str, str]] = []
    provider_order = (
        *CONFIG_SOURCE_PRIORITY,
        *(provider for provider in MODULES if provider not in CONFIG_SOURCE_PRIORITY),
    )
    for provider in provider_order:
        config_path = Path("apps") / provider / f"{app_name}.json"
        if not config_path.is_file():
            continue
        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        package = config.get("package") if isinstance(config, dict) else None
        if package:
            found.append((provider, str(package)))

    packages = {package for _, package in found}
    if len(packages) > 1:
        details = ", ".join(f"{provider}={package}" for provider, package in found)
        raise ValueError(f"conflicting package IDs for {app_name}: {details}")
    return found[0][1] if found else None


def _configured_identity_code(
    app_name: str,
    candidate: VersionCandidate,
) -> str | None:
    """Return a hand-verified code only for the exact same versionName.

    Provider configs sometimes contain a version/version_code pair captured
    from a store release. It is safe enrichment for a patch candidate only
    when the configured versionName is an exact match. This deliberately
    refuses stale pins when patch compatibility moves to another release.

    A non-raw code is already an explicit/configured Android versionCode and
    must remain authoritative. A code attached to raw patch CLI output is
    different: real build logs prove that some patch CLIs put a display/build
    identifier in that position. In that case an exact versionName-matched,
    hand-verified provider pin may replace the CLI value.
    """
    if candidate.code and candidate.raw is None:
        return candidate.code
    provider_order = (
        *CONFIG_SOURCE_PRIORITY,
        *(provider for provider in MODULES if provider not in CONFIG_SOURCE_PRIORITY),
    )
    for provider in provider_order:
        try:
            config = load_config(app_name, provider, allow_synthetic=False)
        except ValueError as error:
            logging.warning("Ignoring invalid identity config for %s: %s", app_name, error)
            continue
        if not config:
            continue
        pinned = pinned_candidate(config)
        if pinned and pinned.code and pinned.name == candidate.name:
            action = "replacing patch CLI id" if candidate.code else "enriching patch release"
            logging.info(
                "🪪 %s: %s %s with configured versionCode %s from %s",
                app_name,
                action,
                candidate.name,
                pinned.code,
                provider,
            )
            return pinned.code
    return candidate.code


def resolve_patch_candidates(
    app_name: str,
    package: str,
    candidates: list[VersionCandidate],
) -> list[VersionCandidate]:
    """Resolve live Android identities without changing patch compatibility.

    Patch CLI output is authoritative for compatibility. Provider metadata is
    allowed only to verify/enrich an already-compatible release identity. A
    provider result that does not match the patch candidate is discarded rather
    than substituted.
    """
    resolved = list(candidates)
    if not package or not resolved:
        return resolved

    # Track which slots received a versionCode from live provider metadata.
    # VersionCandidate.raw deliberately preserves the patch CLI line for audit
    # and matching semantics, so it cannot by itself identify code provenance.
    live_verified = [False] * len(resolved)

    for provider in IDENTITY_RESOLUTION_PRIORITY:
        module = MODULES.get(provider)
        resolver = getattr(module, "resolve_candidate_identities", None) if module else None
        if resolver is None:
            continue
        try:
            proposed = resolver(package, resolved)
        except Exception as error:
            logging.info(
                "Patch identity lookup via %s failed for %s: %s",
                provider,
                app_name,
                error,
            )
            continue
        if not isinstance(proposed, list) or len(proposed) != len(resolved):
            logging.warning(
                "Ignoring malformed patch identity result from %s for %s",
                provider,
                app_name,
            )
            continue

        accepted: list[VersionCandidate] = []
        for index, (requested, candidate) in enumerate(zip(resolved, proposed)):
            if not isinstance(candidate, VersionCandidate):
                accepted.append(requested)
                continue
            if requested.matches(candidate.name, candidate.code):
                if candidate.code and candidate is not requested:
                    live_verified[index] = True
                accepted.append(
                    VersionCandidate(
                        name=candidate.name,
                        code=candidate.code,
                        raw=requested.raw,
                    )
                )
            else:
                logging.warning(
                    "Ignoring %s identity %s for patch-required %s",
                    provider,
                    candidate.describe(),
                    requested.describe(),
                )
                accepted.append(requested)
        resolved = accepted

        # A raw patch CLI code is not evidence that Android versionCode has been
        # resolved. Keep trying later resolvers until every raw identity has
        # either been verified live or is an explicit/non-raw identity.
        if all(
            candidate.code and (candidate.raw is None or live_verified[index])
            for index, candidate in enumerate(resolved)
        ):
            break

    # If live package metadata could not supply a trustworthy versionCode,
    # allow an exact hand-verified provider pin to fill a missing code or
    # replace a raw patch-CLI display id. Never replace a code verified by a
    # live resolver and never carry a code across a versionName change.
    enriched: list[VersionCandidate] = []
    for index, candidate in enumerate(resolved):
        if live_verified[index]:
            enriched.append(candidate)
            continue
        code = _configured_identity_code(app_name, candidate)
        if code and code != candidate.code:
            enriched.append(
                VersionCandidate(name=candidate.name, code=code, raw=candidate.raw)
            )
        else:
            enriched.append(candidate)
    return enriched


def validate_all_configs() -> list[str]:
    """Return human-readable configuration errors without touching the network."""
    errors: list[str] = []
    app_packages: dict[str, set[str]] = {}
    for provider_dir in sorted(Path("apps").iterdir()):
        if not provider_dir.is_dir():
            continue
        provider = provider_dir.name
        if provider not in MODULES:
            errors.append(f"unknown provider directory: {provider_dir}")
            continue
        for config_path in sorted(provider_dir.glob("*.json")):
            app_name = config_path.stem
            try:
                config = load_config(
                    app_name,
                    provider,
                    allow_synthetic=False,
                )
            except ValueError as error:
                errors.append(str(error))
                continue
            package = str(config["package"])
            app_packages.setdefault(app_name, set()).add(package)

    for app_name, packages in sorted(app_packages.items()):
        if len(packages) > 1:
            errors.append(
                f"conflicting package IDs for {app_name}: "
                + ", ".join(sorted(packages))
            )
    return errors


def download_priority(app_name: str) -> tuple[str, ...]:
    """Return the configured download order for an app.

    The public APK sites keep their global order. A provider is moved ahead of
    them only when that app's provider config explicitly opts in with
    ``"primary": true``. This avoids silently changing unrelated apps merely
    because a GitHub config exists.
    """
    primary: list[str] = []
    exclusive: list[str] = []
    for provider in MODULES:
        config_path = Path("apps") / provider / f"{app_name}.json"
        if not config_path.is_file():
            continue
        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            logging.warning(
                "Could not inspect provider priority in %s: %s",
                config_path,
                error,
            )
            continue
        if isinstance(config, dict) and config.get(PRIMARY_PROVIDER_KEY) is True:
            primary.append(provider)
            if config.get("exclusive") is True:
                exclusive.append(provider)

    if exclusive:
        return tuple(dict.fromkeys(exclusive))
    ordered = [*primary, *DOWNLOAD_PRIORITY]
    return tuple(dict.fromkeys(ordered))


def referer(provider: str, app_name: str, config: dict) -> str | None:
    """Return the provider-specific Referer required by download endpoints."""
    if provider == "apkmirror":
        return "https://www.apkmirror.com/"
    if provider == "softonic":
        slug = config.get("name", app_name)
        return f"https://{slug}.en.softonic.com/android"
    if provider == "apkcombo":
        return "https://apkcombo.com/"
    return None


def source_details(provider: str, config: dict | None = None) -> tuple[str, str]:
    """Return a release-note label and URL for the concrete APK source."""
    config = config or {}
    if provider == "github" and config.get("user") and config.get("repo"):
        repository = f"{config['user']}/{config['repo']}"
        return (
            f"GitHub ({repository})",
            f"https://github.com/{repository}/releases",
        )
    return DETAILS.get(provider, (provider, ""))
