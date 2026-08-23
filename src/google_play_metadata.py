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

from src import local_gplaydl_dispenser
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
        details = _current_details(package, arch, dispenser, email)
        if details is None:
            logging.info("Google Play current identity lookup returned no auth token")
            return resolved
    except Exception as error:
        logging.info(
            "Google Play current identity lookup failed for %s: %s",
            package,
            type(error).__name__,
        )
        return resolved

    returned_package = str(getattr(details, "package", "") or package).strip()
    name = str(getattr(details, "version_string", "") or "").strip()
    code = str(getattr(details, "version_code", "") or "").strip()
    if returned_package != package or not name or not code.isdigit():
        logging.info("Google Play current identity was incomplete for %s", package)
        return resolved

    current = VersionCandidate(name=name, code=code)
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
    return resolved
