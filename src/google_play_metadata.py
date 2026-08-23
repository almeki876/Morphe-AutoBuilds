"""Resolve an exact current-release identity from Google Play metadata.

Public APK history services are preferred because they can resolve historical
releases. Their indexes can lag or omit a newly published regional release,
though. In that case Google Play's own details response is a safe final lookup:
it exposes versionName and versionCode together. The result is accepted only
when its versionName exactly matches the patch-required release.
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
    """Yield Play details from each priority device profile.

    Play can expose a staged or device-specific current release.  Each token
    is minted by the same pinned gplaydl implementation; only versionName and
    versionCode are consumed, and individual profile failures are isolated.
    """
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


def resolve_candidate_identities(
    package: str,
    candidates: list[VersionCandidate],
) -> list[VersionCandidate]:
    """Enrich only a patch candidate matching Play's current exact identity."""
    resolved = list(candidates)
    if not package or not resolved or not os.getenv("GPLAYDL_API_KEY", "").strip():
        return resolved

    try:
        local_gplaydl_dispenser.ensure_running()
        arch = os.getenv("GPLAYDL_ARCH", "arm64").strip() or "arm64"
        dispenser = os.getenv("GPLAYDL_DISPENSER_URL", "").strip() or None
        email = (
            os.getenv("GPLAYDL_EMAIL", "").strip()
            or os.getenv("GPLAY_EMAIL", "").strip()
            or None
        )
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
