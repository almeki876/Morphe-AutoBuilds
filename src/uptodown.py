import hashlib
import logging
import re
from datetime import datetime, timezone

from bs4 import BeautifulSoup

from src import utils
from src.versioning import VersionCandidate, parse_candidate


_API_BASE = "https://www.uptodown.app/eapi"
# Reverse-engineered by the maintained justapk Uptodown provider from the
# Uptodown Android client. Keep this isolated so failures fall back to the
# existing public-page discovery path.
_APIKEY_SECRET = "$(=a%·!45J&S"
_DALVIK_UA = (
    "Dalvik/2.1.0 (Linux; U; Android 14; SM-G955F Build/AP2A.240805.005)"
)


def _natural_version_key(version: str) -> tuple:
    """Compare dotted/mixed versions numerically instead of lexicographically."""
    return tuple(
        (0, int(part)) if part.isdigit() else (1, part.casefold())
        for part in re.findall(r"\d+|[^\d]+", version)
    )


def _entry_candidate(entry: dict) -> VersionCandidate | None:
    return parse_candidate(str(entry.get("version", "")))


def _entry_matches(entry: dict, version: str) -> bool:
    identity = _entry_candidate(entry)
    if identity is None:
        return str(entry.get("version", "")) == version
    return version in {
        identity.name,
        identity.code,
        str(entry.get("version", "")),
    }


def _entry_matches_candidate(entry: dict, candidate: VersionCandidate) -> bool:
    identity = _entry_candidate(entry)
    if identity is None:
        return candidate.code is None and candidate.name == str(
            entry.get("version", "")
        )
    return candidate.matches(identity.name, identity.code)


def _generate_api_key(now: datetime | None = None) -> str:
    """Generate Uptodown Android client's hourly API key."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    now = now.astimezone(timezone.utc)
    epoch_ms = int(now.timestamp() * 1000)
    offset_ms = now.minute * 60000 + now.second * 1000 + now.microsecond // 1000
    hour_epoch = (epoch_ms - offset_ms) // 1000
    raw = _APIKEY_SECRET + str(hour_epoch)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _api_headers() -> dict[str, str]:
    return {
        "User-Agent": _DALVIK_UA,
        "Identificador": "Uptodown_Android",
        "Identificador-Version": "707",
        "APIKEY": _generate_api_key(),
    }


def _api_get(path: str):
    return utils.cf_aware_get(
        f"{_API_BASE}{path}",
        headers=_api_headers(),
    )


def _payload_shape(payload) -> str:
    """Describe API structure without logging response values."""
    if isinstance(payload, dict):
        return "dict keys=" + ",".join(sorted(str(key) for key in payload.keys()))
    if isinstance(payload, list):
        return f"list len={len(payload)}"
    return type(payload).__name__


def _api_app_id(package: str) -> int | str | None:
    """Resolve an Uptodown app id without accepting a near package match."""
    response = _api_get(f"/apps/byPackagename/{package}")
    logging.info(
        "Uptodown API byPackagename status=%s package=%s",
        response.status_code,
        package,
    )
    if response.status_code == 200:
        payload = response.json()
        logging.info("Uptodown API byPackagename payload=%s", _payload_shape(payload))
        app = payload.get("data", payload) if isinstance(payload, dict) else None
        if isinstance(app, dict):
            logging.info(
                "Uptodown API byPackagename data keys=%s",
                ",".join(sorted(str(key) for key in app.keys())),
            )
            app_id = app.get("appID") or app.get("id")
            if app_id:
                logging.info("Uptodown API resolved app id from package endpoint")
                return app_id

    response = _api_get(
        f"/v2/apps/search/{package}?page[limit]=5&page[offset]=0"
    )
    logging.info(
        "Uptodown API package search status=%s package=%s",
        response.status_code,
        package,
    )
    if response.status_code != 200:
        return None
    payload = response.json()
    logging.info("Uptodown API package search payload=%s", _payload_shape(payload))
    entries = payload.get("data", []) if isinstance(payload, dict) else []
    if isinstance(entries, dict):
        logging.info(
            "Uptodown API package search data keys=%s",
            ",".join(sorted(str(key) for key in entries.keys())),
        )
        entries = entries.get("results", entries.get("items", []))
    if not isinstance(entries, list):
        logging.info(
            "Uptodown API package search entries type=%s",
            type(entries).__name__,
        )
        return None

    logging.info("Uptodown API package search entries=%d", len(entries))
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        entry_package = entry.get("packageName") or entry.get("packagename")
        if str(entry_package or "") != package:
            continue
        app_id = entry.get("appID") or entry.get("id")
        if app_id:
            logging.info("Uptodown API package search found exact package match")
            return app_id
    logging.info("Uptodown API package search found no exact package match")
    return None


def _api_download_link_for_candidate(
    package: str, candidate: VersionCandidate
) -> str | None:
    """Resolve an exact package/version through Uptodown's current eAPI.

    The API is package-addressed, so unlike HTML slug discovery it can prove we
    are looking at the requested application before selecting a release. The
    caller still performs the repository's normal manifest identity validation
    after download.
    """
    if not package:
        return None

    app_id = _api_app_id(package)
    if not app_id:
        logging.info("Uptodown API could not resolve app id for %s", package)
        return None

    response = _api_get(
        f"/v3/app/{app_id}/device/1/compatible/versions"
        "?page[limit]=50&page[offset]=0"
    )
    logging.info(
        "Uptodown API compatible versions status=%s package=%s",
        response.status_code,
        package,
    )
    if response.status_code != 200:
        return None
    payload = response.json()
    logging.info("Uptodown API compatible versions payload=%s", _payload_shape(payload))
    versions = payload.get("data", []) if isinstance(payload, dict) else []
    if not isinstance(versions, list):
        logging.info(
            "Uptodown API compatible versions data type=%s",
            type(versions).__name__,
        )
        return None

    logging.info("Uptodown API compatible versions entries=%d", len(versions))
    target = next(
        (
            entry
            for entry in versions
            if isinstance(entry, dict) and _entry_matches_candidate(entry, candidate)
        ),
        None,
    )
    if target is None:
        visible = [
            str(entry.get("version", ""))
            for entry in versions[:10]
            if isinstance(entry, dict)
        ]
        logging.info(
            "Uptodown API candidate %s not found; first versions=%s",
            candidate.describe(),
            ",".join(visible),
        )
        return None

    file_id = target.get("fileID") or target.get("fileid")
    if not file_id:
        logging.info(
            "Uptodown API matched candidate but file id missing; keys=%s",
            ",".join(sorted(str(key) for key in target.keys())),
        )
        return None

    response = _api_get(f"/apps/{app_id}/file/{file_id}/downloadUrl?update=0")
    logging.info(
        "Uptodown API download URL status=%s package=%s",
        response.status_code,
        package,
    )
    if response.status_code != 200:
        return None
    payload = response.json()
    logging.info("Uptodown API download URL payload=%s", _payload_shape(payload))
    data = payload.get("data", {}) if isinstance(payload, dict) else {}
    link = data.get("downloadURL") if isinstance(data, dict) else None
    if not link:
        logging.info(
            "Uptodown API download URL missing; data=%s",
            _payload_shape(data),
        )
        return None

    logging.info(
        "✓ Uptodown API resolved %s %s (file %s)",
        package,
        candidate.describe(),
        file_id,
    )
    return str(link)


def _download_url_from_page(page_url: str) -> str | None:
    """Resolve Uptodown's direct file URL from a concrete download page."""
    response = utils.cf_aware_get(page_url)
    response.raise_for_status()
    soup = BeautifulSoup(response.content, "html.parser")
    button = soup.find("button", id="detail-download-button")
    if not button:
        return None

    onclick = button.get("onclick", "")
    if onclick and "download-link-deeplink" in onclick and not page_url.endswith("-x"):
        response = utils.cf_aware_get(page_url + "-x")
        response.raise_for_status()
        soup = BeautifulSoup(response.content, "html.parser")
        button = soup.find("button", id="detail-download-button")

    if button and "data-url" in button.attrs:
        return f"https://dw.uptodown.com/dwn/{button['data-url']}"
    return None


def _download_page_matches_candidate(
    page_url: str, candidate: VersionCandidate
) -> bool:
    """Check the current download page's primary metadata for a release."""
    response = utils.cf_aware_get(page_url)
    if response.status_code != 200:
        return False
    soup = BeautifulSoup(response.content, "html.parser")
    primary_texts: list[str] = []
    if soup.title and soup.title.string:
        primary_texts.append(soup.title.string.strip())
    for attrs in (
        {"property": "og:title"},
        {"name": "twitter:title"},
    ):
        meta = soup.find("meta", attrs=attrs)
        if meta and meta.get("content"):
            primary_texts.append(str(meta["content"]).strip())
    heading = soup.find("h1", id="detail-app-name")
    if heading:
        primary_texts.append(heading.get_text(" ", strip=True))

    # The current release is exposed in the download header as div.version.
    # Do not scan the whole page because it also contains older-version history.
    current_version = soup.select_one("div.version")
    if current_version:
        primary_texts.append(current_version.get_text(" ", strip=True))

    aliases = candidate.aliases("uptodown")
    return any(alias and alias in text for alias in aliases for text in primary_texts)


def get_latest_version(app_name: str, config: dict) -> str:
    possible_names = generate_possible_uptodown_names(config)
    logging.info(f"Trying {len(possible_names)} possible Uptodown names for {app_name}")

    for uptodown_name in possible_names:
        url = f"https://{uptodown_name}.en.uptodown.com/android/versions"
        try:
            response = utils.cf_aware_get(url)
            if response.status_code == 200:
                logging.info(f"✓ Found: {response.url}")
                soup = BeautifulSoup(response.content, "html.parser")
                version_spans = soup.select('#versions-items-list .version')
                versions = [
                    candidate
                    for span in version_spans
                    if span.text.strip()
                    for candidate in [parse_candidate(span.text.strip())]
                    if candidate is not None
                ]

                if versions:
                    highest = max(
                        versions,
                        key=lambda candidate: _natural_version_key(candidate.name),
                    )
                    logging.info(f"Found version {highest.describe()} for {app_name}")
                    return highest.name
            elif response.status_code == 404:
                logging.debug(f"✗ Not found: {url}")
                continue
            elif response.status_code == 410:
                logging.debug(f"✗ Gone (410): {url}")
                continue
            else:
                response.raise_for_status()
        except Exception as e:
            logging.debug(f"Failed for {url}: {str(e)[:50]}...")
            continue

    logging.error(f"Could not find Uptodown page for {app_name}")
    return None


def get_download_link(
    version: str,
    app_name: str,
    config: dict,
    *,
    candidate: VersionCandidate | None = None,
) -> str:
    requested = candidate or VersionCandidate(name=version)
    package = str(config.get("package", ""))

    try:
        api_link = _api_download_link_for_candidate(package, requested)
        if api_link:
            return api_link
    except Exception as error:
        logging.info(
            "Uptodown API lookup failed for %s %s; falling back to public pages: %s",
            app_name,
            requested.describe(),
            utils.safe_text_for_log(error),
        )

    possible_names = generate_possible_uptodown_names(config)
    logging.info(f"Searching {len(possible_names)} possible Uptodown names for {app_name} v{version}")

    for uptodown_name in possible_names:
        base_url = f"https://{uptodown_name}.en.uptodown.com/android"
        try:
            download_page = f"{base_url}/download"
            if _download_page_matches_candidate(download_page, requested):
                current_link = _download_url_from_page(download_page)
                if current_link:
                    logging.info(
                        "✓ Resolved current Uptodown release %s for %s",
                        requested.describe(),
                        app_name,
                    )
                    return current_link

            response = utils.cf_aware_get(f"{base_url}/versions")
            if response.status_code != 200:
                continue

            soup = BeautifulSoup(response.content, "html.parser")
            version_spans = soup.select('#versions-items-list .version')
            visible_versions = [span.text.strip() for span in version_spans if span.text.strip()]

            if visible_versions:
                current = parse_candidate(visible_versions[0])
                if current and requested.matches(current.name, current.code):
                    current_link = _download_url_from_page(download_page)
                    if current_link:
                        logging.info(
                            "✓ Resolved current Uptodown release %s for %s",
                            requested.describe(),
                            app_name,
                        )
                        return current_link

            app_heading = soup.find('h1', id='detail-app-name')
            if not app_heading or 'data-code' not in app_heading.attrs:
                continue
            data_code = app_heading['data-code']

            page = 1
            max_pages = 50
            while page <= max_pages:
                response = utils.cf_aware_get(f"{base_url}/apps/{data_code}/versions/{page}")
                response.raise_for_status()
                version_data = response.json().get('data', [])

                if not version_data:
                    break

                for entry in version_data:
                    if (
                        _entry_matches_candidate(entry, candidate)
                        if candidate is not None
                        else _entry_matches(entry, version)
                    ):
                        version_url_parts = entry["versionURL"]
                        version_url = f"{version_url_parts['url']}/{version_url_parts['extraURL']}/{version_url_parts['versionID']}"
                        download_url = _download_url_from_page(version_url)
                        if download_url:
                            return download_url

                if all(
                    _natural_version_key(
                        (_entry_candidate(entry) or VersionCandidate(
                            name=str(entry.get("version", "0"))
                        )).name
                    )
                    < _natural_version_key(version)
                    for entry in version_data
                ):
                    break
                page += 1
        except Exception as e:
            logging.debug(f"Pattern {uptodown_name} failed: {str(e)[:50]}...")
            continue

    logging.error(f"Version {version} not found for {app_name}")
    return None


def get_download_link_for_candidate(
    candidate: VersionCandidate, app_name: str, config: dict
) -> str | None:
    errors: list[str] = []
    for alias in candidate.aliases("uptodown"):
        try:
            link = get_download_link(alias, app_name, config, candidate=candidate)
            if link:
                return link
        except Exception as error:
            errors.append(f"{alias}: {type(error).__name__}: {error}")
    if errors:
        raise ValueError("; ".join(errors))
    return None


def generate_possible_uptodown_names(config: dict) -> list:
    """Generate deterministic, de-duplicated URL slugs in priority order."""
    app_name = config.get("name", "")
    package = config.get("package", "")
    possible_names: list[str] = []
    seen: set[str] = set()

    def add(candidate: str) -> None:
        candidate = (candidate or "").lower()
        if len(candidate) > 1 and candidate not in seen:
            possible_names.append(candidate)
            seen.add(candidate)

    add(app_name)
    add(app_name.replace("-", ""))
    add(app_name.replace("-plus", "plus"))
    add(app_name.replace("-", "_"))

    package_dash = package.replace(".", "-")
    add(package_dash)

    if package.startswith("com."):
        add(package_dash.replace("com-", ""))
        parts = package.split(".")
        if len(parts) >= 2:
            add(f"com-{parts[1]}")
            add(f"com-{parts[1]}-{parts[-1]}")
            add(parts[1])
            add(parts[-1])
            if len(parts) >= 3:
                add(f"com-{parts[1]}{parts[2]}")
                add(f"com-{parts[1]}{parts[2]}-mea")
                add(f"com-{'-'.join(parts[1:])}")

    suffixes = [
        "",
        "-android",
        "-mobile",
        "-mea",
        "-plus",
        "-pro",
        "-lite",
        "-hd",
        "-apk",
    ]
    for suffix in suffixes:
        add(app_name + suffix)
        add(package_dash + suffix)

    parts = package.split(".")
    if len(parts) >= 2:
        company = parts[1]
        app_basename = parts[-1]
        add(f"{company}-{app_basename}")
        add(f"{company}-{app_name}")
        if "adobe" in package.lower():
            add(f"adobe-{app_basename}")
            add(f"adobe-{app_basename}-mobile")

    for word in ["plus", "pro", "lite", "free", "paid", "mod"]:
        if word in app_name:
            clean = app_name.replace(f"-{word}", "").replace(word, "")
            add(clean)
            add(f"{clean}-{word}")

    return possible_names
