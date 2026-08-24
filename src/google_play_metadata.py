"""Resolve current-release identities from Google Play metadata.

Google Play exposes versionName and versionCode together through its details
response.  This module is the single place that queries that identity so both
patch-version resolution and update monitoring use the same authenticated,
device-profile-aware implementation.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterable
from typing import Any

from src import local_gplaydl_dispenser
from src.gplaydl_profile_retry import ordered_priority_profiles
from src.versioning import VersionCandidate, remember_version_code


def _current_details(
    package: str,
    arch: str,
    dispenser: str | None,
    email: str | None,
):
    """Fetch current details through the pinned gplaydl implementation."""
    from gplaydl.api import get_details
    from gplaydl.auth import ensure_auth

    auth = ensure_auth(
        arch=arch,
        dispenser_url=dispenser,
        email=email,
    )
    return get_details(package, auth) if auth else None


def _profile_details(
    package: str,
    arch: str,
    dispenser: str | None,
    email: str | None,
) -> Iterable[Any]:
    """Yield Play details from each priority device profile."""
    from gplaydl.api import get_details
    from gplaydl.auth import fetch_token_for_profile
    from gplaydl.profiles import get_priority_profiles

    preferred = os.getenv("GPLAYDL_PREFERRED_PROFILE", "").strip() or None
    profiles = ordered_priority_profiles(get_priority_profiles(arch), preferred)
    for key, profile in profiles:
        try:
            auth = fetch_token_for_profile(
                profile,
                dispenser_url=dispenser,
                email=email,
            )
            if auth:
                yield get_details(package, auth)
        except Exception as error:
            logging.info(
                "Google Play current identity profile %s failed for %s: %s",
                key,
                package,
                type(error).__name__,
            )


def _identity(details: Any, package: str) -> VersionCandidate | None:
    returned_package = str(getattr(details, "package", "") or package).strip()
    name = str(getattr(details, "version_string", "") or "").strip()
    code = str(getattr(details, "version_code", "") or "").strip()
    if returned_package != package or not name or not code.isdigit():
        return None
    return VersionCandidate(name=name, code=code)


def _context() -> tuple[str, str | None, str | None] | None:
    if not os.getenv("GPLAYDL_API_KEY", "").strip():
        return None
    local_gplaydl_dispenser.ensure_running()
    arch = os.getenv("GPLAYDL_ARCH", "arm64").strip() or "arm64"
    dispenser = os.getenv("GPLAYDL_DISPENSER_URL", "").strip() or None
    email = (
        os.getenv("GPLAYDL_EMAIL", "").strip()
        or os.getenv("GPLAY_EMAIL", "").strip()
        or None
    )
    return arch, dispenser, email


def current_release_identity(package: str) -> VersionCandidate | None:
    """Return Google Play's current package/versionName/versionCode identity.

    The primary account/profile is preferred. Device-profile variants are
    checked only if the primary details response is unavailable or incomplete.
    No APK bytes are downloaded.
    """
    if not package:
        return None
    try:
        context = _context()
        if context is None:
            return None
        arch, dispenser, email = context
        first = _current_details(package, arch, dispenser, email)
        current = _identity(first, package) if first is not None else None
        if current is not None:
            remember_version_code(package, current.name, current.code or "")
            return current
        for details in _profile_details(package, arch, dispenser, email):
            current = _identity(details, package)
            if current is not None:
                remember_version_code(package, current.name, current.code or "")
                return current
    except Exception as error:
        logging.info(
            "Google Play current identity lookup failed for %s: %s",
            package,
            type(error).__name__,
        )
    return None


def resolve_candidate_identities(
    package: str,
    candidates: list[VersionCandidate],
) -> list[VersionCandidate]:
    """Enrich only patch candidates matching a current Play identity exactly."""
    resolved = list(candidates)
    if not package or not resolved or not os.getenv("GPLAYDL_API_KEY", "").strip():
        return resolved

    try:
        context = _context()
        if context is None:
            return resolved
        arch, dispenser, email = context
        first_details = _current_details(package, arch, dispenser, email)
        if first_details is None:
            logging.info("Google Play current identity lookup returned no auth token")
            return resolved
    except Exception as error:
        logging.info(
            "Google Play current identity lookup failed for %s: %s",
            package,
            type(error).__name__,
        )
        return resolved

    seen: set[tuple[str, str]] = set()
    for details in (first_details,):
        current = _identity(details, package)
        if current is None:
            logging.info("Google Play current identity was incomplete for %s", package)
            continue
        seen.add((current.name, current.code or ""))
        for index, requested in enumerate(candidates):
            if not requested.matches(current.name, current.code):
                continue
            resolved[index] = VersionCandidate(
                name=current.name,
                code=current.code,
                raw=requested.raw,
            )
            remember_version_code(package, current.name, current.code or "")
            logging.info(
                "✓ Google Play current metadata resolved patch-required Android "
                "identity %s -> %s",
                requested.describe(),
                resolved[index].describe(),
            )

    unresolved = [
        requested
        for index, requested in enumerate(candidates)
        if resolved[index].code is None
    ]
    if unresolved:
        try:
            for details in _profile_details(package, arch, dispenser, email):
                current = _identity(details, package)
                if current is None or (current.name, current.code or "") in seen:
                    continue
                seen.add((current.name, current.code or ""))
                for index, requested in enumerate(candidates):
                    if resolved[index].code is not None:
                        continue
                    if not requested.matches(current.name, current.code):
                        continue
                    resolved[index] = VersionCandidate(
                        name=current.name,
                        code=current.code,
                        raw=requested.raw,
                    )
                    remember_version_code(package, current.name, current.code or "")
                    logging.info(
                        "✓ Google Play device-profile metadata resolved "
                        "patch-required Android identity %s -> %s",
                        requested.describe(),
                        resolved[index].describe(),
                    )
                if all(candidate.code is not None for candidate in resolved):
                    break
        except Exception as error:
            logging.info(
                "Google Play device-profile identity lookup failed for %s: %s",
                package,
                type(error).__name__,
            )

    still_unresolved = [
        requested
        for index, requested in enumerate(candidates)
        if resolved[index].code is None
    ]
    if still_unresolved and seen:
        observed = ", ".join(f"{name} ({code})" for name, code in sorted(seen))
        logging.info(
            "Google Play current identities for %s did not exactly match the "
            "patch request: %s",
            package,
            observed,
        )
    return resolved
