"""APKCombo provider with exact package/version matching."""

from __future__ import annotations

import logging
import re
from urllib.parse import parse_qs, urljoin, urlparse

from bs4 import BeautifulSoup

from src import utils
from src.versioning import VersionCandidate


HEADERS = {
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://apkcombo.com/",
}


def _slug(app_name: str, config: dict) -> str:
    return (config.get("name") or app_name).strip().lower().replace("_", "-")


def _old_versions_url(app_name: str, config: dict) -> str:
    return (
        f"https://apkcombo.com/{_slug(app_name, config)}/"
        f"{config['package']}/old-versions/"
    )


def _versions_page(app_name: str, config: dict):
    response = utils.cf_aware_get(
        _old_versions_url(app_name, config),
        headers=HEADERS,
        timeout=30,
    )
    response.raise_for_status()
    # APKCombo canonicalizes an arbitrary slug using the package ID. Confirm
    # the final URL still belongs to the requested package.
    if f"/{config['package']}/" not in response.url:
        raise ValueError(
            f"apkcombo: package mismatch after redirect for '{config['package']}'"
        )
    return response


def _version_anchors(soup: BeautifulSoup, version: str | None = None):
    for anchor in soup.select("a.ver-item[href]"):
        text = anchor.get_text(" ", strip=True)
        if version is None or re.search(
            rf"(?<![\w.]){re.escape(version)}(?![\w.])", text
        ):
            yield anchor


def _anchor_candidate(anchor) -> VersionCandidate | None:
    text = anchor.get_text(" ", strip=True)
    matches = re.findall(r"(?<!\d)(\d+)\s+\((\d[^()]*)\)", text)
    if not matches:
        return None
    code, name = matches[-1]
    return VersionCandidate(name=name, code=code, raw=text)


def get_latest_version(app_name: str, config: dict) -> str | None:
    try:
        response = _versions_page(app_name, config)
        versions: list[VersionCandidate] = []
        for anchor in _version_anchors(
            BeautifulSoup(response.content, "html.parser")
        ):
            candidate = _anchor_candidate(anchor)
            if candidate:
                versions.append(candidate)
        if not versions:
            return None
        versions.sort(
            key=lambda item: utils.normalize_version(item.name),
            reverse=True,
        )
        return versions[0].name
    except Exception as error:
        logging.warning("apkcombo: latest version lookup failed for %s: %s", app_name, error)
        return None


def get_download_link(version: str, app_name: str, config: dict) -> str | None:
    response = _versions_page(app_name, config)
    soup = BeautifulSoup(response.content, "html.parser")
    version_anchor = next(_version_anchors(soup, version), None)
    if version_anchor is None:
        raise ValueError(f"apkcombo: version '{version}' not found for {app_name}")
    return _download_from_anchor(version_anchor, response, version, app_name, config)


def get_download_link_for_candidate(
    candidate: VersionCandidate, app_name: str, config: dict
) -> str | None:
    response = _versions_page(app_name, config)
    soup = BeautifulSoup(response.content, "html.parser")
    aliases = set(candidate.aliases("apkcombo"))
    version_anchor = None
    for anchor in _version_anchors(soup):
        identity = _anchor_candidate(anchor)
        if identity and aliases.intersection({identity.name, identity.code}):
            version_anchor = anchor
            break
    if version_anchor is None:
        raise ValueError(
            f"apkcombo: version '{candidate.describe()}' not found for {app_name}"
        )
    return _download_from_anchor(
        version_anchor,
        response,
        candidate.describe(),
        app_name,
        config,
    )


def _download_from_anchor(
    version_anchor,
    response,
    version: str,
    app_name: str,
    config: dict,
) -> str:
    variant_url = urljoin(response.url, version_anchor["href"])
    variant = utils.cf_aware_get(
        variant_url,
        headers={**HEADERS, "Referer": response.url},
        timeout=30,
    )
    variant.raise_for_status()
    variant_soup = BeautifulSoup(variant.content, "html.parser")
    candidates = variant_soup.select("a[href*='/r2?u=']")
    if not candidates:
        raise ValueError(
            f"apkcombo: no downloadable variant for {app_name} {version}"
        )

    requested_arch = config.get("arch")
    if requested_arch and requested_arch != "universal":
        preferred = [
            anchor
            for anchor in candidates
            if requested_arch in anchor.parent.get_text(" ", strip=True)
        ]
        if preferred:
            candidates = preferred

    redirect_url = urljoin(variant.url, candidates[0]["href"])
    direct_urls = parse_qs(urlparse(redirect_url).query).get("u", [])
    if not direct_urls:
        raise ValueError("apkcombo: signed download URL is missing")
    direct_url = direct_urls[0]
    parsed = urlparse(direct_url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("apkcombo: rejected invalid signed download URL")
    return direct_url
