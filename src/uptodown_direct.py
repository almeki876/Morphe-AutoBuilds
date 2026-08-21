"""Uptodown provider wrapper with current public All variants fallback.

Uptodown's current download page can advertise the exact release while its
primary Download action points at the Uptodown installer rather than the XAPK.
When an explicit Uptodown slug is configured, prefer the public All variants
flow; fall back to the legacy resolver only if that exact flow fails.
"""

from __future__ import annotations

import logging
import re
from urllib.parse import urlparse

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
_POST_DOWNLOAD_RE = re.compile(r"/android/post-download/([^'\"\s?#]+)")
_PATH_RE = re.compile(r"(/[A-Za-z0-9._~!$&'()*+,;=:@%/-]{2,})")


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


def _configured_slugs(config: dict) -> list[str]:
    """Use the explicit Uptodown slug when one is configured."""
    configured = str(config.get("name") or "").strip()
    if configured:
        return [configured]
    return [str(slug) for slug in legacy.generate_possible_uptodown_names(config)]


def _post_download_token(variant) -> str | None:
    """Read the modern post-download token embedded in one variant card."""
    match = _POST_DOWNLOAD_RE.search(str(variant))
    return match.group(1) if match else None


def _safe_variant_shape(variant) -> str:
    """Describe variant markup without logging download tokens or URLs."""
    variant_attrs = sorted(str(key) for key in variant.attrs)
    descendants: list[str] = []
    data_keys: set[str] = set()
    href_paths: set[str] = set()
    onclick_paths: set[str] = set()

    nodes = [variant, *variant.find_all(True)]
    for node in nodes:
        classes = node.get("class") or []
        class_text = ".".join(str(value) for value in classes[:4])
        descriptor = str(node.name)
        if class_text:
            descriptor += f".{class_text}"
        if descriptor not in descendants:
            descendants.append(descriptor)

        for key in node.attrs:
            key_text = str(key)
            if key_text.startswith("data-"):
                data_keys.add(key_text)

        href = node.get("href")
        if href:
            try:
                path = urlparse(str(href)).path
            except Exception:
                path = ""
            if path:
                href_paths.add(path[:180])

        onclick = str(node.get("onclick") or "")
        for path in _PATH_RE.findall(onclick):
            # Paths may contain opaque download tokens. Keep only the route
            # shape by replacing long path segments with a placeholder.
            safe_segments = []
            for segment in path.split("/"):
                if len(segment) > 24:
                    safe_segments.append("<opaque>")
                else:
                    safe_segments.append(segment)
            onclick_paths.add("/".join(safe_segments)[:180])

    return (
        f"variant_attrs={variant_attrs} "
        f"descendants={descendants[:16]} "
        f"data_keys={sorted(data_keys)} "
        f"href_paths={sorted(href_paths)[:8]} "
        f"onclick_paths={sorted(onclick_paths)[:8]}"
    )


def _direct_from_post_download(base_url: str, token: str) -> str | None:
    """Resolve one modern Uptodown post-download token to the CDN URL."""
    response = utils.cf_aware_get(f"{base_url}/post-download/{token}")
    logging.info(
        "Uptodown post-download status=%s via %s",
        response.status_code,
        utils.safe_url_for_log(base_url),
    )
    if response.status_code != 200:
        return None
    soup = BeautifulSoup(response.content, "html.parser")
    node = soup.select_one(".post-download[data-url]")
    if not node:
        # Some frontends still expose the same token on the legacy button.
        node = soup.select_one("#detail-download-button[data-url]")
    data_url = str(node.get("data-url", "")).strip() if node else ""
    if not data_url:
        logging.info("Uptodown post-download response had no direct data-url")
        return None
    return f"https://dw.uptodown.com/dwn/{data_url}"


def _direct_link_from_variants(
    candidate: VersionCandidate,
    app_name: str,
    config: dict,
) -> str | None:
    """Resolve a direct XAPK/APK URL via Uptodown's public All variants flow."""
    for slug in _configured_slugs(config):
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

                file_ids: list[tuple[str, bool, str | None]] = []
                for variant_index, variant in enumerate(files_soup.select(".variant")):
                    logging.info(
                        "Uptodown variant structure #%d for %s via %s: %s",
                        variant_index,
                        app_name,
                        utils.safe_url_for_log(base_url),
                        _safe_variant_shape(variant),
                    )
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
                    file_ids.append((file_id, is_xapk, _post_download_token(variant)))

                logging.info(
                    "Uptodown All variants found %d file candidates for %s via %s",
                    len(file_ids),
                    app_name,
                    utils.safe_url_for_log(base_url),
                )
                file_ids.sort(key=lambda item: item[1], reverse=True)
                for file_id, is_xapk, post_token in file_ids:
                    if post_token:
                        try:
                            direct = _direct_from_post_download(base_url, post_token)
                        except Exception as error:
                            logging.info(
                                "Uptodown post-download failed for %s file %s: %s",
                                app_name,
                                file_id,
                                utils.safe_text_for_log(error),
                            )
                            direct = None
                        if direct:
                            logging.info(
                                "✓ Uptodown All variants resolved %s %s "
                                "through post-download (file %s via %s)",
                                app_name,
                                candidate.describe(),
                                file_id,
                                utils.safe_url_for_log(base_url),
                            )
                            return direct

                    # Older/current alternate layouts expose the CDN token on a
                    # concrete /download/<file-id> page. Retain that fallback.
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


def _legacy_candidate_link(
    candidate: VersionCandidate,
    app_name: str,
    config: dict,
) -> str | None:
    try:
        return legacy.get_download_link_for_candidate(candidate, app_name, config)
    except Exception as error:
        logging.info(
            "Legacy Uptodown resolver failed for %s %s: %s",
            app_name,
            candidate.describe(),
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
    if config.get("name"):
        direct = _direct_link_from_variants(requested, app_name, config)
        if direct:
            return direct
    link = legacy.get_download_link(
        version,
        app_name,
        config,
        candidate=requested,
    )
    if link:
        return link
    if not config.get("name"):
        return _direct_link_from_variants(requested, app_name, config)
    return None


def get_download_link_for_candidate(
    candidate: VersionCandidate,
    app_name: str,
    config: dict,
) -> str | None:
    # A configured slug is direct evidence of the intended Uptodown listing.
    # Try that exact public All variants route before generic legacy guesses.
    if config.get("name"):
        direct = _direct_link_from_variants(candidate, app_name, config)
        if direct:
            return direct
    link = _legacy_candidate_link(candidate, app_name, config)
    if link:
        return link
    if not config.get("name"):
        return _direct_link_from_variants(candidate, app_name, config)
    return None


def generate_possible_uptodown_names(config: dict) -> list:
    return legacy.generate_possible_uptodown_names(config)
