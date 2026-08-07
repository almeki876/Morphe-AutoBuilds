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

from src import apkcombo, apkmirror, apkpure, aptoide, github, softonic, uptodown


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
    "apkmirror": apkmirror,
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
    {"apkmirror", "softonic", "aptoide", "apkcombo"}
)

# Search existing app configs in deterministic order when deriving a package ID.
CONFIG_SOURCE_PRIORITY = (
    "apkmirror",
    "apkpure",
    "uptodown",
    "aptoide",
    "github",
)


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
