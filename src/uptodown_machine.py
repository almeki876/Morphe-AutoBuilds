"""Machine-readable discovery fallback for Uptodown.

Keep the existing Uptodown provider as the primary implementation.  When its
public-page/API routes cannot resolve an exact historical release, inspect
structured metadata already exposed by the same Uptodown pages: JSON-LD,
embedded JSON and advertised RSS/Atom feeds.  This reduces dependence on CSS
class names without bypassing bot challenges or weakening APK identity checks.

Every URL returned here is restricted to HTTPS Uptodown hosts.  The caller still
downloads through the repository's hardened downloader and validates the actual
APK/XAPK manifest package, versionName and versionCode before accepting it.
"""

from __future__ import annotations

import json
import logging
import re
from urllib.parse import urljoin, urlparse
from xml.etree import ElementTree

from bs4 import BeautifulSoup

from src import uptodown as legacy
from src import uptodown_direct as base
from src import utils
from src.versioning import VersionCandidate


_FEED_TYPES = {
    "application/rss+xml",
    "application/atom+xml",
    "application/feed+json",
}
_VERSION_KEYS = (
    "softwareVersion",
    "version",
    "versionName",
    "name",
    "headline",
)
_URL_KEYS = (
    "downloadUrl",
    "contentUrl",
    "installUrl",
    "url",
    "sameAs",
)
_MAX_SCRIPT_CHARS = 1_000_000
_MAX_FEED_BYTES = 2_000_000


def _uptodown_url(raw: object, base_url: str) -> str | None:
    """Return one normalized HTTPS Uptodown URL, rejecting foreign hosts."""
    value = str(raw or "").strip()
    if not value:
        return None
    absolute = urljoin(base_url, value)
    parsed = urlparse(absolute)
    hostname = (parsed.hostname or "").casefold()
    if parsed.scheme != "https":
        return None
    if hostname != "uptodown.com" and not hostname.endswith(".uptodown.com"):
        return None
    return absolute


def _candidate_in_text(text: object, candidate: VersionCandidate) -> bool:
    value = " ".join(str(text or "").split())
    if not value:
        return False
    for alias in dict.fromkeys(candidate.aliases("uptodown")):
        alias = str(alias or "").strip()
        if not alias:
            continue
        # Avoid a target such as 11.4.5 matching 11.4.50 while still allowing
        # surrounding labels like "Lightroom 11.4.5 APK".
        if re.search(rf"(?<![\w.]){re.escape(alias)}(?![\w.])", value):
            return True
    return False


def _json_candidate_urls(
    value: object,
    candidate: VersionCandidate,
    page_url: str,
) -> list[str]:
    """Extract exact-release URLs from one decoded JSON object.

    A URL is accepted only from a dictionary that also carries a version-like
    field matching the requested candidate.  This prevents unrelated page URLs
    elsewhere in a large hydration payload from being treated as release links.
    """
    found: list[str] = []

    def walk(node: object) -> None:
        if isinstance(node, list):
            for child in node:
                walk(child)
            return
        if not isinstance(node, dict):
            return

        version_values = [node.get(key) for key in _VERSION_KEYS if key in node]
        if any(_candidate_in_text(item, candidate) for item in version_values):
            for key in _URL_KEYS:
                if key not in node:
                    continue
                raw_urls = node[key] if isinstance(node[key], list) else [node[key]]
                for raw_url in raw_urls:
                    safe = _uptodown_url(raw_url, page_url)
                    if safe and safe not in found:
                        found.append(safe)

        for child in node.values():
            if isinstance(child, (dict, list)):
                walk(child)

    walk(value)
    return found


def _structured_urls(
    soup: BeautifulSoup,
    page_url: str,
    candidate: VersionCandidate,
) -> list[str]:
    """Read JSON-LD and page-hydration JSON without relying on DOM classes."""
    found: list[str] = []
    for script in soup.find_all("script"):
        script_type = str(script.get("type") or "").casefold().split(";", 1)[0]
        if script_type not in {"application/ld+json", "application/json"}:
            continue
        raw = script.string if script.string is not None else script.get_text()
        raw = str(raw or "").strip()
        if not raw or len(raw) > _MAX_SCRIPT_CHARS:
            continue
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        for url in _json_candidate_urls(payload, candidate, page_url):
            if url not in found:
                found.append(url)
    return found


def _feed_urls(soup: BeautifulSoup, page_url: str) -> list[str]:
    """Return RSS/Atom/JSON Feed URLs explicitly advertised by the page."""
    found: list[str] = []
    for link in soup.find_all("link", href=True):
        rel = {str(value).casefold() for value in (link.get("rel") or [])}
        media_type = str(link.get("type") or "").casefold().split(";", 1)[0]
        if "alternate" not in rel or media_type not in _FEED_TYPES:
            continue
        safe = _uptodown_url(link.get("href"), page_url)
        if safe and safe not in found:
            found.append(safe)
        if len(found) >= 4:
            break
    return found


def _xml_local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].casefold()


def _feed_candidate_urls(
    content: bytes,
    feed_url: str,
    candidate: VersionCandidate,
) -> list[str]:
    """Extract exact-version entry links from RSS or Atom XML."""
    if not content or len(content) > _MAX_FEED_BYTES:
        return []
    try:
        root = ElementTree.fromstring(content)
    except ElementTree.ParseError:
        return []

    found: list[str] = []
    for entry in root.iter():
        if _xml_local_name(str(entry.tag)) not in {"item", "entry"}:
            continue
        text = " ".join(
            str(node.text or "").strip()
            for node in entry.iter()
            if str(node.text or "").strip()
        )
        if not _candidate_in_text(text, candidate):
            continue

        for node in entry.iter():
            local = _xml_local_name(str(node.tag))
            raw_urls: list[object] = []
            if local == "link":
                raw_urls.extend([node.attrib.get("href"), node.text])
            elif local == "enclosure":
                raw_urls.append(node.attrib.get("url"))
            for raw_url in raw_urls:
                safe = _uptodown_url(raw_url, feed_url)
                if safe and safe not in found:
                    found.append(safe)
    return found


def _json_feed_candidate_urls(
    content: bytes,
    feed_url: str,
    candidate: VersionCandidate,
) -> list[str]:
    """Extract an exact-version entry URL from a JSON Feed document."""
    if not content or len(content) > _MAX_FEED_BYTES:
        return []
    try:
        payload = json.loads(content)
    except (TypeError, ValueError, json.JSONDecodeError, UnicodeDecodeError):
        return []
    if not isinstance(payload, dict):
        return []

    found: list[str] = []
    items = payload.get("items", [])
    if not isinstance(items, list):
        return []
    for item in items:
        if not isinstance(item, dict):
            continue
        text = " ".join(
            str(item.get(key) or "")
            for key in ("title", "summary", "content_text", "content_html")
        )
        if not _candidate_in_text(text, candidate):
            continue
        for key in ("url", "external_url"):
            safe = _uptodown_url(item.get(key), feed_url)
            if safe and safe not in found:
                found.append(safe)
    return found


def _resolve_release_url(url: str) -> str | None:
    """Resolve one structured-data URL to a direct Uptodown CDN download."""
    parsed = urlparse(url)
    hostname = (parsed.hostname or "").casefold()
    if hostname == "dw.uptodown.com" and parsed.path.startswith("/dwn/"):
        return url
    try:
        return legacy._download_url_from_page(url)
    except Exception as error:
        logging.info(
            "Uptodown structured release page failed at %s: %s",
            utils.safe_url_for_log(url),
            utils.safe_text_for_log(error),
        )
        return None


def _structured_download_link(
    candidate: VersionCandidate,
    app_name: str,
    config: dict,
) -> str | None:
    """Resolve a release from structured metadata on official Uptodown pages."""
    try:
        slugs = base._configured_slugs(config)
    except Exception:
        slugs = base.generate_possible_uptodown_names(config)

    for slug in list(dict.fromkeys(str(value) for value in slugs if value))[:12]:
        for base_url in base._base_urls(slug):
            versions_url = f"{base_url}/versions"
            try:
                response = utils.cf_aware_get(versions_url)
                if response.status_code != 200:
                    continue
                soup = BeautifulSoup(response.content, "html.parser")

                for url in _structured_urls(soup, versions_url, candidate):
                    direct = _resolve_release_url(url)
                    if direct:
                        logging.info(
                            "✓ Uptodown structured metadata resolved %s %s via %s",
                            app_name,
                            candidate.describe(),
                            utils.safe_url_for_log(base_url),
                        )
                        return direct

                for feed_url in _feed_urls(soup, versions_url):
                    feed = utils.cf_aware_get(feed_url)
                    if feed.status_code != 200:
                        continue
                    content_type = str(feed.headers.get("content-type", "")).casefold()
                    if "json" in content_type or feed_url.casefold().endswith(".json"):
                        entry_urls = _json_feed_candidate_urls(
                            feed.content, feed_url, candidate
                        )
                    else:
                        entry_urls = _feed_candidate_urls(feed.content, feed_url, candidate)
                    for url in entry_urls:
                        direct = _resolve_release_url(url)
                        if direct:
                            logging.info(
                                "✓ Uptodown advertised feed resolved %s %s via %s",
                                app_name,
                                candidate.describe(),
                                utils.safe_url_for_log(feed_url),
                            )
                            return direct
            except Exception as error:
                logging.info(
                    "Uptodown structured discovery failed for %s via %s: %s",
                    app_name,
                    utils.safe_url_for_log(base_url),
                    utils.safe_text_for_log(error),
                )
    return None


def get_latest_version(app_name: str, config: dict) -> str | None:
    return base.get_latest_version(app_name, config)


def get_download_link(
    version: str,
    app_name: str,
    config: dict,
    *,
    candidate: VersionCandidate | None = None,
) -> str | None:
    requested = candidate or VersionCandidate(name=version)
    link = base.get_download_link(
        version,
        app_name,
        config,
        candidate=requested,
    )
    if link:
        return link
    return _structured_download_link(requested, app_name, config)


def get_download_link_for_candidate(
    candidate: VersionCandidate,
    app_name: str,
    config: dict,
) -> str | None:
    link = base.get_download_link_for_candidate(candidate, app_name, config)
    if link:
        return link
    return _structured_download_link(candidate, app_name, config)


def generate_possible_uptodown_names(config: dict) -> list:
    return base.generate_possible_uptodown_names(config)


def __getattr__(name: str):
    return getattr(base, name)
