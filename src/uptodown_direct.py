"""Uptodown provider wrapper with current public All variants fallback.

Uptodown's current download page can advertise the exact release while its
primary Download action points at the Uptodown installer rather than the XAPK.
When the legacy eAPI/public-page resolver cannot produce a direct file URL,
resolve the release through the site's All variants flow instead.
"""

from __future__ import annotations

import logging

from bs4 import BeautifulSoup

from src import uptodown as legacy
from src import utils
from src.versioning import VersionCandidate


# These are official Uptodown locale frontends observed for the same app/release.
# Keep the English host first, then try independently routed locale hosts when a
# GitHub runner cannot reach that frontend through Cloudflare.
_UPTODOWN_HOST_TEMPLATES = (
    "{slug}.en.uptodown.com",
    "{slug}.br.uptodown.com",
    "{slug}.de.uptodown.com",
    "{slug}.uptodown.com",
)


def _page_matches_candidate(soup: BeautifulSoup, candidate: VersionCandidate) -> bool:
    primary_texts: list[str] = []
    if soup.title and soup.title.string:
        primary_texts.append(soup.title.string.strip())
    current_version = soup.select_one("div.version")
    if current_version:
        primary_texts.append(current_version.get_text(" ", strip=True))
    for attrs in ({"property": "og:title"}, {"name": "twitter:title"}):
        meta = soup.find("meta", attrs=attrs)
        if meta and meta.get("content"):
            primary_texts.append(str(meta["content"]).strip())
    return any(
        alias and alias in text
        for alias in candidate.aliases("uptodown")
        for text in primary_texts
    )


def _base_urls(slug: str) -> list[str]:
    return [
        f"https://{template.format(slug=slug)}/android"
        for template in _UPTODOWN_HOST_TEMPLATES
    ]


def _direct_link_from_variants(
    candidate: VersionCandidate,
    app_name: str,
    config: dict,
) -> str | None:
    """Resolve a direct XAPK/APK URL via Uptodown's public All variants flow."""
    for slug in legacy.generate_possible_uptodown_names(config):
        for base_url in _base_urls(slug):
            download_page = f"{base_url}/download"
            try:
                response = utils.cf_aware_get(download_page)
                logging.info(
                    "Uptodown All variants page status=%s for %s via %s",
                    response.status_code,
                    app_name,
                    utils.safe_url_for_log(base_url),
                )
                if response.status_code != 200:
                    continue
                soup = BeautifulSoup(response.content, "html.parser")
                if not _page_matches_candidate(soup, candidate):
                    logging.info(
                        "Uptodown All variants page did not identify %s for %s via %s",
                        candidate.describe(),
                        app_name,
                        utils.safe_url_for_log(base_url),
                    )
                    continue

                app_heading = soup.find(id="detail-app-name")
                data_code = app_heading.get("data-code") if app_heading else None
                variants = soup.select_one(
                    ".button.variants[data-version], .variants[data-version]"
                )
                data_version = variants.get("data-version") if variants else None

                # Some layouts keep data-code on the versions page instead of the
                # current download page. Do not infer it from a near package/slug.
                if not data_code:
                    versions_response = utils.cf_aware_get(f"{base_url}/versions")
                    logging.info(
                        "Uptodown All variants versions status=%s for %s via %s",
                        versions_response.status_code,
                        app_name,
                        utils.safe_url_for_log(base_url),
                    )
                    if versions_response.status_code == 200:
                        versions_soup = BeautifulSoup(
                            versions_response.content, "html.parser"
                        )
                        versions_heading = versions_soup.find(id="detail-app-name")
                        if versions_heading:
                            data_code = versions_heading.get("data-code")

                if not data_code or not data_version:
                    logging.info(
                        "Uptodown All variants metadata unavailable for %s (%s): "
                        "data_code=%s data_version=%s",
                        app_name,
                        utils.safe_url_for_log(base_url),
                        bool(data_code),
                        bool(data_version),
                    )
                    continue

                files_response = utils.cf_aware_get(
                    f"{base_url.rsplit('/android', 1)[0]}/app/{data_code}/version/"
                    f"{data_version}/files"
                )
                if files_response.status_code != 200:
                    logging.info(
                        "Uptodown All variants files status=%s for %s via %s",
                        files_response.status_code,
                        app_name,
                        utils.safe_url_for_log(base_url),
                    )
                    continue
                payload = files_response.json()
                content = payload.get("content", "") if isinstance(payload, dict) else ""
                files_soup = BeautifulSoup(str(content), "html.parser")

                file_ids: list[tuple[str, bool]] = []
                for variant in files_soup.select(".variant"):
                    report = variant.select_one(".v-report[data-file-id]")
                    if not report:
                        continue
                    file_id = str(report.get("data-file-id", "")).strip()
                    if not file_id:
                        continue
                    file_type = variant.select_one(".v-file > span")
                    is_xapk = bool(
                        file_type
                        and file_type.get_text(" ", strip=True).casefold() == "xapk"
                    )
                    file_ids.append((file_id, is_xapk))

                logging.info(
                    "Uptodown All variants found %d file candidates for %s via %s",
                    len(file_ids),
                    app_name,
                    utils.safe_url_for_log(base_url),
                )
                # Prefer the XAPK variant because the verified Amazon Shopping
                # 32.13.2.100 artifact is published by Uptodown as XAPK.
                file_ids.sort(key=lambda item: item[1], reverse=True)
                for file_id, is_xapk in file_ids:
                    suffixes = ("-x", "") if is_xapk else ("", "-x")
                    for suffix in suffixes:
                        variant_page = f"{base_url}/download/{file_id}{suffix}"
                        try:
                            direct = legacy._download_url_from_page(variant_page)
                        except Exception as error:
                            logging.info(
                                "Uptodown variant page failed for %s file %s: %s",
                                app_name,
                                file_id,
                                utils.safe_text_for_log(error),
                            )
                            direct = None
                        if direct:
                            logging.info(
                                "✓ Uptodown All variants resolved %s %s (file %s via %s)",
                                app_name,
                                candidate.describe(),
                                file_id,
                                utils.safe_url_for_log(base_url),
                            )
                            return direct
            except Exception as error:
                logging.info(
                    "Uptodown All variants failed for %s (%s): %s",
                    app_name,
                    utils.safe_url_for_log(base_url),
                    utils.safe_text_for_log(error),
                )
    return None


def get_latest_version(app_name: str, config: dict) -> str:
    return legacy.get_latest_version(app_name, config)


def get_download_link(
    version: str,
    app_name: str,
    config: dict,
    *,
    candidate: VersionCandidate | None = None,
) -> str | None:
    requested = candidate or VersionCandidate(name=version)
    link = legacy.get_download_link(
        version,
        app_name,
        config,
        candidate=requested,
    )
    if link:
        return link
    return _direct_link_from_variants(requested, app_name, config)


def get_download_link_for_candidate(
    candidate: VersionCandidate,
    app_name: str,
    config: dict,
) -> str | None:
    # Call legacy once for the complete candidate so aliases do not multiply
    # expensive network attempts before trying the public variants fallback.
    try:
        link = legacy.get_download_link_for_candidate(candidate, app_name, config)
    except Exception as error:
        logging.info(
            "Legacy Uptodown resolver failed for %s %s: %s",
            app_name,
            candidate.describe(),
            utils.safe_text_for_log(error),
        )
        link = None
    if link:
        return link
    return _direct_link_from_variants(candidate, app_name, config)


def generate_possible_uptodown_names(config: dict) -> list:
    return legacy.generate_possible_uptodown_names(config)
