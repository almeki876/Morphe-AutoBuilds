"""Softonic APK provider.

Softonic uses per-app subdomains and may protect its final download page with
a JavaScript challenge. This provider resolves exact-version links from the
public app page; the shared downloader rejects challenge HTML automatically
and continues to the next provider.
"""

from __future__ import annotations

import logging
import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from src import utils


HEADERS = {
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://en.softonic.com/android",
}


def _slug(app_name: str, config: dict) -> str:
    return (config.get("name") or app_name).strip().lower().replace("_", "-")


def _app_url(app_name: str, config: dict) -> str:
    return f"https://{_slug(app_name, config)}.en.softonic.com/android"


def _page(app_name: str, config: dict):
    response = utils.cf_aware_get(
        _app_url(app_name, config),
        headers=HEADERS,
        timeout=30,
    )
    response.raise_for_status()
    package = config.get("package", "")
    if package and package not in response.text:
        raise ValueError(
            f"softonic: resolved page does not contain package '{package}'"
        )
    return response


def _version_links(soup: BeautifulSoup) -> list[tuple[str, str]]:
    versions: list[tuple[str, str]] = []
    for anchor in soup.find_all("a", href=True):
        href = anchor["href"]
        match = re.search(r"/post-download/v/([^/?#]+)", href)
        if match:
            versions.append((match.group(1), href))
    return list(dict.fromkeys(versions))


def get_latest_version(app_name: str, config: dict) -> str | None:
    try:
        response = _page(app_name, config)
        versions = [version for version, _ in _version_links(
            BeautifulSoup(response.content, "html.parser")
        )]
        return utils.get_highest_version(versions)
    except Exception as error:
        logging.warning("softonic: latest version lookup failed for %s: %s", app_name, error)
        return None


def get_download_link(version: str, app_name: str, config: dict) -> str | None:
    response = _page(app_name, config)
    for found_version, href in _version_links(
        BeautifulSoup(response.content, "html.parser")
    ):
        if found_version == version:
            return urljoin(response.url, href)
    raise ValueError(f"softonic: version '{version}' not found for {app_name}")
