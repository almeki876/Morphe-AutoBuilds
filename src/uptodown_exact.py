"""Exact Uptodown history lookup layered on the existing hardened provider.

Uptodown's eAPI exposes Android versionName and versionCode as separate fields.
Patch compatibility is the release-selection source of truth; this module can
therefore enrich a patch-compatible versionName or versionCode with the other
half of the Android release identity before Google Play or mirror downloads are
attempted. Download resolution keeps the same exact matching, bounded history
pagination, hardened HTTP handling, and public-page fallback.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from urllib.parse import urlparse

from src import uptodown as legacy
from src import uptodown_machine as fallback
from src import utils
from src.versioning import VersionCandidate, remember_version_code


_API_PAGE_LIMIT = 50
_API_MAX_PAGES = 20
_OFFICIAL_DOWNLOAD_HOST_SUFFIXES = ("uptodown.com", "uptodown.net")


def _entry_identity(entry: dict) -> VersionCandidate | None:
    """Return the Android release identity represented by one eAPI row."""
    name = str(entry.get("version") or entry.get("versionName") or "").strip()
    raw_code = entry.get("versionCode")
    if raw_code is None:
        raw_code = entry.get("versioncode")
    code = str(raw_code).strip() if raw_code is not None else None
    if not name:
        return None
    try:
        return VersionCandidate(name=name, code=code)
    except ValueError:
        return None


def _entry_matches_candidate(entry: dict, candidate: VersionCandidate) -> bool:
    """Compare separate eAPI versionName/versionCode fields exactly."""
    identity = _entry_identity(entry)
    return bool(identity and candidate.matches(identity.name, identity.code))


def _iter_api_version_entries(
    package: str,
    *,
    app_id: int | str | None = None,
) -> Iterator[dict]:
    """Yield bounded eAPI history rows for one exact Android package."""
    if not package:
        return

    resolved_app_id = app_id or legacy._api_app_id(package)
    if not resolved_app_id:
        return

    offset = 0
    seen_offsets: set[int] = set()
    for page_index in range(_API_MAX_PAGES):
        if offset in seen_offsets:
            break
        seen_offsets.add(offset)

        response = legacy._api_get(
            f"/v3/app/{resolved_app_id}/device/1/compatible/versions"
            f"?page[limit]={_API_PAGE_LIMIT}&page[offset]={offset}"
        )
        logging.info(
            "Uptodown exact eAPI versions status=%s package=%s page=%d offset=%d",
            response.status_code,
            package,
            page_index + 1,
            offset,
        )
        if response.status_code != 200:
            return

        payload = response.json()
        versions = payload.get("data", []) if isinstance(payload, dict) else []
        if not isinstance(versions, list) or not versions:
            break

        for entry in versions:
            if isinstance(entry, dict):
                yield entry

        if len(versions) < _API_PAGE_LIMIT:
            break
        offset += len(versions)


def resolve_candidate_identities(
    package: str,
    candidates: list[VersionCandidate],
) -> list[VersionCandidate]:
    """Enrich patch-compatible releases with live versionName/versionCode pairs.

    The patch candidate remains authoritative: a provider row is accepted only
    when ``VersionCandidate.matches`` proves it is the same release. A name-only
    patch requirement may therefore learn its Android versionCode, while a
    code-only patch requirement may learn its real versionName. Nearby releases
    can never replace the patch-required release.
    """
    if not candidates:
        return []

    resolved = list(candidates)
    pending = set(range(len(candidates)))
    for entry in _iter_api_version_entries(package):
        identity = _entry_identity(entry)
        if identity is None:
            continue

        for index in list(pending):
            requested = candidates[index]
            if not requested.matches(identity.name, identity.code):
                continue
            resolved[index] = VersionCandidate(
                name=identity.name,
                code=identity.code,
                raw=requested.raw,
            )
            if identity.code:
                remember_version_code(package, identity.name, identity.code)
            logging.info(
                "✓ Resolved patch-required Android identity %s -> %s",
                requested.describe(),
                resolved[index].describe(),
            )
            pending.remove(index)

        if not pending:
            break

    return resolved


def _safe_download_url(value: object) -> str | None:
    """Accept only HTTPS download URLs on Uptodown-owned domains."""
    url = str(value or "").strip()
    if not url:
        return None
    parsed = urlparse(url)
    host = (parsed.hostname or "").casefold().rstrip(".")
    if parsed.scheme != "https":
        return None
    if not any(
        host == suffix or host.endswith(f".{suffix}")
        for suffix in _OFFICIAL_DOWNLOAD_HOST_SUFFIXES
    ):
        return None
    return url


def _exact_api_download_link(
    package: str,
    candidate: VersionCandidate,
) -> str | None:
    """Resolve an exact release from bounded, paginated Uptodown eAPI history."""
    if not package:
        return None

    app_id = legacy._api_app_id(package)
    if not app_id:
        return None
    target = next(
        (
            entry
            for entry in _iter_api_version_entries(package, app_id=app_id)
            if _entry_matches_candidate(entry, candidate)
        ),
        None,
    )
    if target is None:
        return None

    file_id = target.get("fileID") or target.get("fileid")
    if not file_id:
        return None
    download_response = legacy._api_get(
        f"/apps/{app_id}/file/{file_id}/downloadUrl?update=0"
    )
    logging.info(
        "Uptodown exact eAPI download URL status=%s package=%s",
        download_response.status_code,
        package,
    )
    if download_response.status_code != 200:
        return None
    download_payload = download_response.json()
    data = (
        download_payload.get("data", {})
        if isinstance(download_payload, dict)
        else {}
    )
    link = (
        _safe_download_url(data.get("downloadURL"))
        if isinstance(data, dict)
        else None
    )
    if link:
        logging.info(
            "✓ Uptodown exact eAPI resolved %s %s",
            package,
            candidate.describe(),
        )
    return link


def _try_exact_api(
    candidate: VersionCandidate,
    app_name: str,
    config: dict,
) -> str | None:
    package = str(config.get("package") or "").strip()
    try:
        return _exact_api_download_link(package, candidate)
    except Exception as error:
        logging.info(
            "Uptodown exact eAPI failed for %s %s; using public fallback: %s",
            app_name,
            candidate.describe(),
            utils.safe_text_for_log(error),
        )
        return None


def get_latest_version(app_name: str, config: dict) -> str | None:
    return fallback.get_latest_version(app_name, config)


def get_download_link(
    version: str,
    app_name: str,
    config: dict,
    *,
    candidate: VersionCandidate | None = None,
) -> str | None:
    requested = candidate or VersionCandidate(name=version)
    link = _try_exact_api(requested, app_name, config)
    if link:
        return link
    return fallback.get_download_link(
        version,
        app_name,
        config,
        candidate=requested,
    )


def get_download_link_for_candidate(
    candidate: VersionCandidate,
    app_name: str,
    config: dict,
) -> str | None:
    link = _try_exact_api(candidate, app_name, config)
    if link:
        return link
    return fallback.get_download_link_for_candidate(candidate, app_name, config)


def generate_possible_uptodown_names(config: dict) -> list:
    return fallback.generate_possible_uptodown_names(config)


def __getattr__(name: str):
    return getattr(fallback, name)
