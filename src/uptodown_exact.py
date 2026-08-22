"""Exact Uptodown history lookup layered on the existing hardened provider.

Uptodown's eAPI exposes Android versionName and versionCode in separate fields.
The legacy matcher only parses the display-version string, so a candidate that
correctly pins both fields can be missed.  This wrapper preserves the existing
HTTP/challenge handling, performs exact two-field matching, paginates bounded
history, and falls back to the normal public-page resolver.
"""

from __future__ import annotations

import logging
from urllib.parse import urlparse

from src import uptodown as legacy
from src import uptodown_machine as fallback
from src import utils
from src.versioning import VersionCandidate


_API_PAGE_LIMIT = 50
_API_MAX_PAGES = 20
_OFFICIAL_DOWNLOAD_HOST_SUFFIXES = ("uptodown.com", "uptodown.net")


def _entry_matches_candidate(entry: dict, candidate: VersionCandidate) -> bool:
    """Compare separate eAPI versionName/versionCode fields exactly."""
    name = str(entry.get("version") or entry.get("versionName") or "").strip()
    raw_code = entry.get("versionCode")
    if raw_code is None:
        raw_code = entry.get("versioncode")
    code = str(raw_code).strip() if raw_code is not None else None
    return candidate.matches(name, code)


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

    offset = 0
    seen_offsets: set[int] = set()
    for page_index in range(_API_MAX_PAGES):
        if offset in seen_offsets:
            break
        seen_offsets.add(offset)

        response = legacy._api_get(
            f"/v3/app/{app_id}/device/1/compatible/versions"
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
            return None

        payload = response.json()
        versions = payload.get("data", []) if isinstance(payload, dict) else []
        if not isinstance(versions, list) or not versions:
            break

        target = next(
            (
                entry
                for entry in versions
                if isinstance(entry, dict)
                and _entry_matches_candidate(entry, candidate)
            ),
            None,
        )
        if target is not None:
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
            return None

        if len(versions) < _API_PAGE_LIMIT:
            break
        offset += len(versions)

    return None


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
