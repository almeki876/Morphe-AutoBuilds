"""Resolve patch-required Android versionCode values before Google Play download.

Google Play historical downloads require Android ``versionCode`` while patch
bundles commonly expose only ``versionName``. This module keeps that mapping
runtime-only and provider-driven: no app-specific versionCode table is stored in
the repository.
"""

from __future__ import annotations

import logging

from src.versioning import VersionCandidate, discovered_version_code


class VersionCodeResolutionError(RuntimeError):
    """Raised when an explicit patch version cannot be mapped to versionCode."""


def resolve_candidate(package: str, candidate: VersionCandidate | None) -> VersionCandidate | None:
    """Return a Play-ready candidate with an exact versionCode when required.

    ``None`` means the patch side has no version restriction
    (``any``/``null``), so the current Google Play release is intentionally
    used. Explicit candidates must carry a trustworthy Android versionCode
    before gplaydl is invoked; otherwise gplaydl would fetch the current release
    and only fail after downloading the wrong APK.
    """
    if candidate is None:
        return None

    # A non-raw candidate comes from explicit provider/config identity metadata
    # rather than unverified patch CLI display text, so its code is authoritative.
    # Raw patch output is deliberately re-verified: several patch sources print
    # numeric build/display identifiers that are not AndroidManifest versionCode.
    if candidate.code and candidate.raw is None:
        return candidate

    remembered = discovered_version_code(package, candidate.name)
    if remembered:
        logging.info(
            "🪪 Google Play reused dynamically discovered versionCode %s for %s %s",
            remembered,
            package,
            candidate.name,
        )
        return VersionCandidate(name=candidate.name, code=remembered, raw=candidate.raw)

    # Import lazily to avoid a registry import cycle during src initialization.
    from src import providers

    # Prefer the registry's dedicated identity resolvers, then automatically use
    # any future provider that implements the same hook. This keeps the policy
    # generic as providers are added or removed.
    current = candidate
    for provider_name in providers.identity_resolution_order():
        module = providers.MODULES.get(provider_name)
        resolver = getattr(module, "resolve_candidate_identities", None) if module else None
        if resolver is None:
            continue
        try:
            proposed = resolver(package, [current])
        except Exception as error:
            logging.info(
                "Google Play versionCode lookup via %s failed for %s %s: %s",
                provider_name,
                package,
                current.name,
                error,
            )
            continue
        if not isinstance(proposed, list) or len(proposed) != 1:
            continue
        resolved = proposed[0]
        if not isinstance(resolved, VersionCandidate):
            continue
        if not current.matches(resolved.name, resolved.code):
            logging.warning(
                "Ignoring %s version identity %s for requested %s",
                provider_name,
                resolved.describe(),
                current.describe(),
            )
            continue
        if resolved.code:
            logging.info(
                "🪪 Google Play dynamically resolved %s -> versionCode %s via %s",
                current.name,
                resolved.code,
                provider_name,
            )
            return VersionCandidate(
                name=resolved.name,
                code=resolved.code,
                raw=current.raw,
            )

    # Public history indexes can lag a new or regional Play release. Google
    # Play's own details response safely fills that gap only for its current
    # release: both versionName and versionCode must exactly match the patch
    # candidate. Historical mismatches still fail closed below.
    from src import google_play_metadata

    proposed = google_play_metadata.resolve_candidate_identities(package, [current])
    if len(proposed) == 1 and isinstance(proposed[0], VersionCandidate):
        resolved = proposed[0]
        if current.matches(resolved.name, resolved.code) and resolved.code:
            logging.info(
                "🪪 Google Play dynamically resolved %s -> versionCode %s "
                "via current Play metadata",
                current.name,
                resolved.code,
            )
            return VersionCandidate(
                name=resolved.name,
                code=resolved.code,
                raw=current.raw,
            )

    raise VersionCodeResolutionError(
        f"could not dynamically resolve Android versionCode for {package} {candidate.name}; "
        "refusing to fetch Google Play current release for an explicitly versioned patch"
    )
