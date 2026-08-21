"""APKMirror provider wrapper with cross-page latest-version selection.

APKMirror can expose different release subsets in its app-specific uploads feed,
canonical app page, and search pages. The base provider historically returned
as soon as the first source yielded any version, which could make an older
uploads-feed entry win over a newer canonical release. This wrapper preserves
all base-provider behaviour while selecting the highest version observed across
all safe discovery sources.
"""

from __future__ import annotations

from src import apkmirror as _base
from src import utils


def get_latest_version(app_name: str, config: dict) -> str | None:
    sources: list[str] = []
    uploads_url = _base._configured_uploads_url(config)
    if uploads_url:
        sources.append(uploads_url)
    configured_url = _base._configured_app_url(config)
    if configured_url:
        sources.append(configured_url)
    sources.extend(
        _base._search_url(query)
        for query in dict.fromkeys(
            value
            for value in (config.get("package"), config.get("name"), app_name)
            if value
        )
    )

    versions: list[str] = []
    for url in dict.fromkeys(sources):
        discovered = _base._discovery_page(url)
        if discovered:
            soup, final_url = discovered
            uploads = _base._uploads_url(soup, final_url)
            if uploads:
                uploads_page = _base._discovery_page(uploads)
                if uploads_page:
                    soup, _ = uploads_page
            versions.extend(_base._versions_from_release_anchors(soup, config))
        if _base._DISCOVERY_BLOCKED:
            break

    return utils.get_highest_version(list(dict.fromkeys(versions)))


def __getattr__(name: str):
    """Delegate every other provider operation to the base APKMirror module."""
    return getattr(_base, name)
