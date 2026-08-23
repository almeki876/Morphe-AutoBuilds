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
import json
import os
import re
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from src import uptodown as legacy
from src import uptodown_machine as fallback
from src import utils
from src.versioning import VersionCandidate, remember_version_code


_API_PAGE_LIMIT = 50
_API_MAX_PAGES = 20
_OFFICIAL_DOWNLOAD_HOST_SUFFIXES = ("uptodown.com", "uptodown.net")
_CONFIG_DIR = Path("apps/uptodown")
_SHA256_RE = re.compile(r"\b[0-9a-fA-F]{64}\b")


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

    if pending:
        _resolve_current_page_identities(package, candidates, resolved, pending)

    return resolved


def _configured_slugs(package: str) -> list[str]:
    """Return exact configured Uptodown slugs for one package."""
    slugs: list[str] = []
    if not _CONFIG_DIR.is_dir():
        return slugs
    for path in sorted(_CONFIG_DIR.glob("*.json")):
        try:
            config = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(config, dict) or str(config.get("package") or "") != package:
            continue
        slug = str(config.get("name") or "").strip()
        if slug and slug not in slugs:
            slugs.append(slug)
    return slugs


def _row_value(soup: BeautifulSoup, label: str) -> str | None:
    wanted = label.casefold()
    for row in soup.select("tr"):
        cells = row.find_all(["th", "td"])
        if len(cells) < 2:
            continue
        if cells[-2].get_text(" ", strip=True).casefold() != wanted:
            continue
        value = cells[-1].get_text(" ", strip=True)
        if value:
            return value
    return None


def _current_page_hash(
    package: str,
    candidate: VersionCandidate,
    slug: str,
) -> str | None:
    """Read an exact current release SHA-256 from public Uptodown HTML."""
    url = f"https://{slug}.en.uptodown.com/android/download"
    response = utils.cf_aware_get(url, timeout=30, retries=2)
    logging.info(
        "Uptodown exact current metadata status=%s package=%s",
        response.status_code,
        package,
    )
    if response.status_code != 200:
        return None
    soup = BeautifulSoup(response.content, "html.parser")
    if _row_value(soup, "Package Name") != package:
        return None

    primary: list[str] = []
    if soup.title and soup.title.string:
        primary.append(soup.title.string.strip())
    current = soup.select_one("div.version")
    if current:
        primary.append(current.get_text(" ", strip=True))
    for attrs in ({"property": "og:title"}, {"name": "twitter:title"}):
        meta = soup.find("meta", attrs=attrs)
        if meta and meta.get("content"):
            primary.append(str(meta["content"]).strip())
    if not any(
        re.search(
            rf"(?<![0-9A-Za-z]){re.escape(alias)}(?![0-9A-Za-z])",
            text,
            flags=re.IGNORECASE,
        )
        for alias in candidate.aliases("uptodown")
        for text in primary
    ):
        return None

    digest_text = _row_value(soup, "SHA256") or ""
    match = _SHA256_RE.search(digest_text)
    return match.group(0).casefold() if match else None


def _resolve_current_page_identities(
    package: str,
    candidates: list[VersionCandidate],
    resolved: list[VersionCandidate],
    pending: set[int],
) -> None:
    """Enrich current releases via provider SHA-256 and VT manifest metadata."""
    if not os.getenv("VIRUSTOTAL_API_KEY", "").strip():
        return
    from src import virustotal_identity

    for slug in _configured_slugs(package):
        for index in list(pending):
            requested = candidates[index]
            try:
                digest = _current_page_hash(package, requested, slug)
                if not digest:
                    continue
                identities = virustotal_identity.identities_for_sha256(
                    digest,
                    package,
                )
            except Exception as error:
                logging.info(
                    "Uptodown/VirusTotal identity lookup failed for %s %s: %s",
                    package,
                    requested.describe(),
                    type(error).__name__,
                )
                continue
            identity = next(
                (
                    item
                    for item in identities
                    if requested.matches(item.name, item.code)
                ),
                None,
            )
            if identity is None or not identity.code:
                continue
            resolved[index] = VersionCandidate(
                name=identity.name,
                code=identity.code,
                raw=requested.raw,
            )
            remember_version_code(package, identity.name, identity.code)
            pending.remove(index)
            logging.info(
                "✓ Uptodown SHA-256 and VirusTotal manifest resolved exact "
                "Android identity %s -> %s",
                requested.describe(),
                resolved[index].describe(),
            )
        if not pending:
            break


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
