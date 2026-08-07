"""APKMirror provider.

Release URLs are discovered from APKMirror's own app/search pages instead of
being guessed from titles. This survives publisher/app renames and avoids the
large bursts of predictable 404 requests that trigger anti-bot protection.
"""

from __future__ import annotations

import logging
import os
import random
import re
import time
from urllib.parse import urlencode, urljoin, urlparse

from bs4 import BeautifulSoup

from src import utils


BASE_URL = "https://www.apkmirror.com"
HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": f"{BASE_URL}/",
}
_RELEASE_HREF_RE = re.compile(r"^/apk/[^/]+/[^/]+/[^/]+-release/?$")
_DISCOVERY_PAGES: dict[str, BeautifulSoup | None] = {}
_DISCOVERY_FINAL_URLS: dict[str, str] = {}
_DISCOVERY_BLOCKED = False
_LAST_REQUEST_AT = 0.0


def _throttle() -> None:
    """Space page requests so parallel Actions jobs do not trigger 429 bursts."""
    global _LAST_REQUEST_AT
    try:
        interval = max(
            0.0,
            float(os.getenv("APKMIRROR_REQUEST_INTERVAL_SECONDS", "2.0")),
        )
    except ValueError:
        interval = 2.0
    remaining = interval - (time.monotonic() - _LAST_REQUEST_AT)
    if remaining > 0:
        time.sleep(remaining + random.uniform(0.0, 0.4))
    _LAST_REQUEST_AT = time.monotonic()


def _get(url: str, referer: str | None = None, retries: int | None = None):
    _throttle()
    headers = dict(HEADERS)
    if referer:
        headers["Referer"] = referer
    response = utils.cf_aware_get(
        url,
        headers=headers,
        timeout=30,
        retries=retries,
    )
    response.raise_for_status()
    return response


def _discovery_page(url: str) -> tuple[BeautifulSoup, str] | None:
    """Fetch an app/search page once per process and stop after a hard block."""
    global _DISCOVERY_BLOCKED
    if _DISCOVERY_BLOCKED:
        return None
    if url in _DISCOVERY_PAGES:
        soup = _DISCOVERY_PAGES[url]
        if soup is None:
            return None
        return soup, _DISCOVERY_FINAL_URLS[url]

    try:
        # One retry is enough here: there are multiple discovery routes and
        # repeating the same 403 for every compatible version worsens blocking.
        response = _get(url, retries=2)
        soup = BeautifulSoup(response.content, "html.parser")
        _DISCOVERY_PAGES[url] = soup
        _DISCOVERY_FINAL_URLS[url] = response.url
        return soup, response.url
    except Exception as error:
        _DISCOVERY_PAGES[url] = None
        if "403" in str(error):
            _DISCOVERY_BLOCKED = True
            logging.warning(
                "APKMirror blocked discovery requests for this job; "
                "switching providers without further request bursts"
            )
        logging.warning("APKMirror discovery page failed at %s: %s", url, error)
        return None


def _clean_version(version: str) -> str:
    version = re.sub(r"\s+build\s+\d+$", "", version, flags=re.IGNORECASE)
    version = re.sub(r"\(\d+\)$", "", version)
    return version.strip()


def _version_matches(text: str, version: str) -> bool:
    candidates = {version, version.removesuffix("-release")}
    return any(
        re.search(rf"(?<![\w.]){re.escape(candidate)}(?![\w.])", text)
        for candidate in candidates
        if candidate
    )


def _configured_app_url(config: dict) -> str | None:
    org = config.get("org")
    name = config.get("name")
    if not org or not name:
        return None
    return f"{BASE_URL}/apk/{org}/{name}/"


def _release_links(soup: BeautifulSoup, version: str) -> list[tuple[str, str]]:
    links: list[tuple[str, str]] = []
    seen: set[str] = set()
    for anchor in soup.find_all("a", href=True):
        href = anchor["href"]
        parsed_path = urlparse(urljoin(BASE_URL, href)).path
        if not _RELEASE_HREF_RE.fullmatch(parsed_path):
            continue
        context = anchor.get_text(" ", strip=True)
        row = anchor.find_parent("div", class_=re.compile(r"\btable-row\b"))
        if row:
            context = f"{context} {row.get_text(' ', strip=True)}"
        if not _version_matches(context, version):
            continue
        absolute = urljoin(BASE_URL, href.split("#", 1)[0])
        if absolute not in seen:
            links.append((absolute, context))
            seen.add(absolute)
    return links


def _search_url(query: str) -> str:
    params = urlencode(
        {
            "post_type": "app_release",
            "searchtype": "apk",
            "s": query,
        }
    )
    return f"{BASE_URL}/?{params}"


def _score_release_link(link: tuple[str, str], config: dict) -> int:
    url, context = link
    value = f"{url} {context}".casefold()
    score = 0
    org = (config.get("org") or "").casefold()
    name = (config.get("name") or "").casefold()
    if org and f"/apk/{org}/" in value:
        score += 20
    if name and name in value:
        score += 15
    if " beta" not in value and "-beta-" not in value:
        score += 8
    if not any(marker in value for marker in ("amazon", "f-droid", "fire tv")):
        score += 12
    return score


def _discover_release(version: str, app_name: str, config: dict) -> str | None:
    configured_url = _configured_app_url(config)
    if configured_url:
        discovered = _discovery_page(configured_url)
        if discovered:
            soup, _ = discovered
            links = _release_links(
                soup, version
            )
            if links:
                chosen = max(links, key=lambda item: _score_release_link(item, config))
                logging.info("APKMirror release discovered from app page: %s", chosen[0])
                return chosen[0]

    # Package search repairs stale publisher/app slugs and also enables
    # APKMirror for apps without a hand-written apps/apkmirror JSON file.
    queries = [config.get("package"), config.get("name"), app_name]
    for query in dict.fromkeys(value for value in queries if value):
        discovered = _discovery_page(_search_url(query))
        if discovered:
            soup, _ = discovered
            links = _release_links(
                soup, version
            )
            if links:
                chosen = max(links, key=lambda item: _score_release_link(item, config))
                logging.info(
                    "APKMirror release discovered by search '%s': %s",
                    query,
                    chosen[0],
                )
                return chosen[0]
        if _DISCOVERY_BLOCKED:
            break
    return None


def _variant_score(row_text: str, config: dict, target_arch: str) -> int:
    text = " ".join(row_text.split()).casefold()
    score = 0

    configured_type = str(config.get("type", "APK")).casefold()
    if configured_type in text:
        score += 50
    elif "apk" in text:
        score += 35
    elif "bundle" in text:
        # Bundles are supported: the build pipeline merges them with APKEditor.
        score += 25

    arch = (target_arch or "universal").casefold()
    if arch in text:
        score += 35
    elif "universal" in text:
        score += 30
    elif arch == "universal" and "arm64-v8a" in text:
        score += 20
    elif any(part.strip() in text for part in arch.split("+")):
        score += 15

    dpi = str(config.get("dpi", "nodpi")).casefold()
    if dpi in text:
        score += 20
    elif "nodpi" in text:
        score += 15
    elif re.search(r"\d+(?:-\d+)?dpi", text):
        score += 8

    if "beta" in text:
        score -= 5
    return score


def _select_variant(
    soup: BeautifulSoup,
    version: str,
    config: dict,
    target_arch: str,
) -> str | None:
    candidates: list[tuple[int, str, str]] = []
    for row in soup.select("div.table-row"):
        text = " ".join(row.get_text(" ", strip=True).split())
        if not _version_matches(text, version):
            continue
        anchors = [
            anchor
            for anchor in row.find_all("a", href=True)
            if "apk-download" in anchor["href"]
        ]
        if not anchors:
            continue
        url = urljoin(BASE_URL, anchors[0]["href"])
        candidates.append((_variant_score(text, config, target_arch), url, text))

    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0], reverse=True)
    score, url, description = candidates[0]
    logging.info(
        "APKMirror selected variant (score=%d): %s [%s]",
        score,
        url,
        description[:180],
    )
    return url


def _final_download_link(variant_url: str) -> str | None:
    variant = _get(variant_url)
    soup = BeautifulSoup(variant.content, "html.parser")

    # On some layouts the variant page is already the final keyed page.
    direct = soup.select_one("a#download-link[href]")
    if direct:
        return urljoin(variant.url, direct["href"])

    button = (
        soup.select_one("a.downloadButton[href]")
        or soup.select_one("a[href*='/download/?key=']")
    )
    if not button:
        raise ValueError("APKMirror variant page has no download button")

    keyed_url = urljoin(variant.url, button["href"])
    keyed = _get(keyed_url, referer=variant.url)
    keyed_soup = BeautifulSoup(keyed.content, "html.parser")
    direct = (
        keyed_soup.select_one("a#download-link[href]")
        or keyed_soup.select_one("a[rel='nofollow'][href]")
    )
    if not direct:
        raise ValueError("APKMirror keyed page has no final download link")
    return urljoin(keyed.url, direct["href"])


def get_download_link(
    version: str,
    app_name: str,
    config: dict,
    arch: str | None = None,
) -> str | None:
    clean_version = _clean_version(version)
    release_url = _discover_release(clean_version, app_name, config)
    if not release_url:
        logging.warning(
            "APKMirror release not found for %s %s", app_name, clean_version
        )
        return None

    try:
        release = _get(release_url)
        soup = BeautifulSoup(release.content, "html.parser")
        if not _version_matches(soup.title.get_text(" ", strip=True), clean_version):
            raise ValueError("release title does not match requested version")
        package = config.get("package")
        if package and package not in release.text:
            raise ValueError(
                f"release package does not match requested package '{package}'"
            )
        target_arch = arch or config.get("arch", "universal")
        variant_url = _select_variant(soup, clean_version, config, target_arch)
        if not variant_url:
            raise ValueError("release contains no compatible downloadable variant")
        return _final_download_link(variant_url)
    except Exception as error:
        logging.warning(
            "APKMirror download flow failed for %s %s: %s",
            app_name,
            clean_version,
            error,
        )
        return None


def get_latest_version(app_name: str, config: dict) -> str | None:
    sources = []
    configured_url = _configured_app_url(config)
    if configured_url:
        sources.append(configured_url)
    sources.extend(
        _search_url(query)
        for query in dict.fromkeys(
            value
            for value in (config.get("package"), config.get("name"), app_name)
            if value
        )
    )

    for url in sources:
        discovered = _discovery_page(url)
        if discovered:
            soup, _ = discovered
            versions = []
            for row in soup.select("div.table-row"):
                text = " ".join(row.get_text(" ", strip=True).split())
                if " beta" in text.casefold():
                    continue
                for match in re.finditer(r"(?<!\d)(\d+(?:\.\d+)+)(?!\d)", text):
                    versions.append(match.group(1))
                    break
            latest = utils.get_highest_version(list(dict.fromkeys(versions)))
            if latest:
                return latest
        if _DISCOVERY_BLOCKED:
            break
    return None
