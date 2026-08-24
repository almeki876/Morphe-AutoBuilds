"""Narrow compatibility wrapper for gplaydl acquisition profile retries.

``gplaydl==4.2.1`` already rotates device profiles while minting a token, but
its download flow retries profiles only when Play reports that an app is
invisible or incompatible.  A delivery status of ``not purchased`` exits after
the first accepted profile even though a freshly checked-in device/profile can
be served differently (notably for restricted banking apps).

This module patches only the CLI's private ``_acquire`` seam.  Details,
purchase, delivery, APK downloads, and Google Play digest verification remain
owned by the pinned upstream package.  The patch is version/signature guarded
so an upstream change fails closed instead of silently running stale glue.
"""

from __future__ import annotations

import inspect
import os
from collections.abc import Iterable
from importlib import metadata
from typing import Any

SUPPORTED_GPLAYDL_VERSION = "4.2.1"
PREFERRED_PROFILE_ENV = "GPLAYDL_PREFERRED_PROFILE"
DEFAULT_PLAY_LOCALES = ["en-US", "ja"]
_ACQUIRE_PARAMETERS = (
    "package",
    "version",
    "arch",
    "auth_data",
    "dispenser",
    "email",
    "locales",
)


def ordered_priority_profiles(
    profiles: Iterable[tuple[str, dict[str, Any]]],
    preferred_key: str | None = None,
) -> list[tuple[str, dict[str, Any]]]:
    """Return each profile once, optionally moving a known key to the front."""
    unique: list[tuple[str, dict[str, Any]]] = []
    seen: set[str] = set()
    for key, profile in profiles:
        if key not in seen:
            unique.append((key, profile))
            seen.add(key)

    preferred = (preferred_key or "").strip()
    if not preferred:
        return unique
    return sorted(unique, key=lambda item: 0 if item[0] == preferred else 1)


def _safe_profile_label(key: str, profile: dict[str, Any]) -> str:
    """Return only public bundled-profile metadata suitable for CI logs."""
    name = str(profile.get("UserReadableName", key)).strip() or key
    return f"{key} ({name})"


def acquire_after_not_purchased(
    package: str,
    version: int | None,
    arch: str,
    dispenser: str | None,
    email: str | None,
    locales: list[str] | None = None,
):
    """Try every priority profile with a newly minted GSF ID and token.

    The local dispenser performs a fresh Google check-in for every
    ``fetch_token_for_profile`` call.  No token, email, GSF ID, dispenser URL,
    or response body is included in logs or the final error.
    """
    from gplaydl.api import (
        AppNotAvailableError,
        AppNotPurchasedError,
        AppNotSupportedError,
        PlayAPIError,
        get_delivery,
        get_details,
        purchase,
    )
    from gplaydl.auth import DispenserError, fetch_token_for_profile
    from gplaydl.profiles import get_priority_profiles
    from rich import print as rprint

    locales = list(locales) if locales else list(DEFAULT_PLAY_LOCALES)
    preferred = os.getenv(PREFERRED_PROFILE_ENV, "").strip() or None
    profiles = ordered_priority_profiles(get_priority_profiles(arch), preferred)
    attempted: list[str] = []

    for key, profile in profiles:
        label = _safe_profile_label(key, profile)
        try:
            # The dispenser's Mint flow starts with check-in, so every attempt
            # receives a new GSF identity as well as a fresh OAuth token.
            auth = fetch_token_for_profile(
                profile,
                dispenser_url=dispenser,
                email=email,
            )
            if not auth:
                attempted.append(f"{key}:token-unavailable")
                rprint(f"[dim]Google Play profile {label}: token unavailable[/dim]")
                continue

            details = get_details(package, auth)
            version_code = version or details.version_code
            if not version_code:
                attempted.append(f"{key}:no-version")
                continue

            delivery_token = purchase(package, version_code, auth)
            delivery = get_delivery(
                package,
                version_code,
                auth,
                delivery_token,
                locales,
            )
            rprint(f"[dim]Google Play acquisition served by profile {label}[/dim]")
            return details, version_code, delivery
        except AppNotPurchasedError:
            attempted.append(f"{key}:not-purchased")
        except AppNotSupportedError:
            attempted.append(f"{key}:not-supported")
        except AppNotAvailableError:
            attempted.append(f"{key}:not-available")
        except DispenserError:
            attempted.append(f"{key}:token-refused")
        except PlayAPIError as error:
            attempted.append(f"{key}:{type(error).__name__}")

    summary = ", ".join(attempted) or "no priority profiles available"
    raise AppNotPurchasedError(
        f"The account could not acquire {package} with any fresh priority "
        f"device profile ({summary})."
    )


def install_cli_patch() -> None:
    """Install the guarded acquisition retry around gplaydl's CLI seam."""
    import gplaydl.cli as cli
    from gplaydl.api import AppNotPurchasedError

    try:
        version = metadata.version("gplaydl")
    except metadata.PackageNotFoundError:
        version = ""
    if version != SUPPORTED_GPLAYDL_VERSION:
        raise RuntimeError(
            "gplaydl profile retry supports exactly "
            f"{SUPPORTED_GPLAYDL_VERSION}, found {version or 'unknown'}"
        )

    original = cli._acquire
    parameters = tuple(inspect.signature(original).parameters)
    if parameters != _ACQUIRE_PARAMETERS:
        raise RuntimeError(
            "gplaydl _acquire signature changed; refusing to apply stale "
            "profile-retry compatibility patch"
        )

    if getattr(original, "_morphe_profile_retry", False):
        return

    def patched_acquire(
        package,
        version,
        arch,
        auth_data,
        dispenser,
        email,
        locales=None,
    ):
        locales = list(locales) if locales else list(DEFAULT_PLAY_LOCALES)
        try:
            return original(
                package,
                version,
                arch,
                auth_data,
                dispenser,
                email,
                locales,
            )
        except AppNotPurchasedError:
            cli.rprint(
                "[yellow]The first fresh Google Play device was not allowed "
                "to acquire this app; trying every priority device profile "
                "with a new check-in...[/yellow]"
            )
            return acquire_after_not_purchased(
                package,
                version,
                arch,
                dispenser,
                email,
                locales,
            )

    patched_acquire._morphe_profile_retry = True  # type: ignore[attr-defined]
    cli._acquire = patched_acquire


def main() -> None:
    """Run the pinned upstream CLI with the narrow retry installed."""
    install_cli_patch()
    from gplaydl.cli import app

    app()


if __name__ == "__main__":
    main()
