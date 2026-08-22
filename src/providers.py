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


DOWNLOAD_PRIORITY = (
    "apkmirror",
    "apkpure",
    "uptodown",
    "softonic",
    "aptoide",
    "apkcombo",
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
