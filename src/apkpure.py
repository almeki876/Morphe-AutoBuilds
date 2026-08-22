import logging
import re
from urllib.parse import urlencode

from src import utils
from src.versioning import (
    VersionCandidate,
    discovered_version_code,
    remember_version_code,
)
from bs4 import BeautifulSoup

SITE_BASE_URL = "https://apkpure.com"
DOWNLOAD_BASE_URL = "https://d.apkpure.net/b"
HISTORY_API_URL = "https://tapi.pureapk.com/v3/get_app_his_version"

# curl-cffi supplies a User-Agent matching its TLS browser impersonation.
# Overriding it with an old Chrome version creates a detectable mismatch and
# can itself trigger APKPure's anti-bot 403 response.
HEADERS = {
    'Accept-Language': 'en-US,en;q=0.9',
    'Referer': f'{SITE_BASE_URL}/'
}
# Keep the history API request separate from APKPure's website headers. Public
# clients such as Obtainium use only these API-specific headers; adding a web
# Referer can put the request on a different edge path even when HTTP is 200.
HISTORY_HEADERS = {
    "Ual-Access-Businessid": "projecta",
    "Ual-Access-ProjectA": '{"device_info":{"os_ver":"35"}}',
}

# APKPureのリクエストタイムアウト（秒）
# デフォルト無制限だと30秒以上かかる場合があるため短縮
TIMEOUT = 15


def _history_rows_from_payload(payload: object) -> list[dict]:
    """Extract version rows from known APKPure history response envelopes.

    APKPure's public client response is normally ``{"version_list": [...]}``.
    Edge/API gateways have also wrapped successful payloads below one dict
    (for example ``data`` or ``result``). Accept only a list explicitly named
    ``version_list`` and only dictionaries that carry the two Android release
    identity fields we need. This stays generic without recursively pairing
    unrelated values from a large response.
    """
    if not isinstance(payload, dict):
        return []

    containers: list[dict] = [payload]
    for key in ("data", "result"):
        value = payload.get(key)
        if isinstance(value, dict):
            containers.append(value)

    for container in containers:
        rows = container.get("version_list")
        if not isinstance(rows, list):
            continue
        valid = [
            row
            for row in rows
            if isinstance(row, dict)
            and "version_name" in row
            and "version_code" in row
        ]
        if valid:
            return valid
    return []


def _history_payload_shape(payload: object) -> str:
    """Describe response structure without logging release data or URLs."""
    if not isinstance(payload, dict):
        return type(payload).__name__
    top = sorted(str(key) for key in payload.keys())[:20]
    nested: list[str] = []
    for key in ("data", "result"):
        value = payload.get(key)
        if isinstance(value, dict):
            nested.append(f"{key}={sorted(str(k) for k in value.keys())[:20]}")
    version_list = payload.get("version_list")
    count = len(version_list) if isinstance(version_list, list) else None
    details = [f"keys={top}"]
    if count is not None:
        details.append(f"version_list_count={count}")
    details.extend(nested)
    return " ".join(details)


def _history_entries(package: str) -> list[dict]:
    """Return APKPure's package-addressed historical release metadata.

    The public history API exposes Android ``version_name`` and ``version_code``
    separately. It is used only as release identity metadata; APK bytes still
    come from authenticated Google Play whenever Play is enabled.
    """
    response = utils.cf_aware_get(
        f"{HISTORY_API_URL}?{urlencode({'package_name': package, 'hl': 'en'})}",
        headers=HISTORY_HEADERS,
        timeout=TIMEOUT,
        retries=2,
    )
    logging.info(
        "APKPure history metadata status=%s package=%s",
        response.status_code,
        package,
    )
    if response.status_code != 200:
        return []
    try:
        payload = response.json()
    except Exception as error:
        logging.info("APKPure history metadata parse failed for %s: %s", package, error)
        return []

    rows = _history_rows_from_payload(payload)
    if not rows:
        logging.info(
            "APKPure history metadata had no usable version rows for %s: %s",
            package,
            _history_payload_shape(payload),
        )
    else:
        logging.info(
            "APKPure history metadata found %d release row(s) for %s",
            len(rows),
            package,
        )
    return rows


def _history_identity(row: dict, package: str) -> VersionCandidate | None:
    returned_package = str(row.get("package_name") or package).strip()
    if returned_package != package:
        return None
    name = str(row.get("version_name") or "").strip()
    code = str(row.get("version_code") or "").strip()
    if not name or not code.isdigit():
        return None
    try:
        return VersionCandidate(name=name, code=code)
    except ValueError:
        return None


def resolve_candidate_identities(
    package: str,
    candidates: list[VersionCandidate],
) -> list[VersionCandidate]:
    """Enrich exact patch versionNames with APKPure Android versionCodes."""
    if not candidates:
        return []
    try:
        rows = _history_entries(package)
    except Exception as error:
        logging.info("APKPure history identity lookup failed for %s: %s", package, error)
        return list(candidates)

    resolved = list(candidates)
    pending = set(range(len(candidates)))
    for row in rows:
        identity = _history_identity(row, package)
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
            remember_version_code(package, identity.name, identity.code or "")
            logging.info(
                "✓ APKPure resolved patch-required Android identity %s -> %s",
                requested.describe(),
                resolved[index].describe(),
            )
            pending.remove(index)
        if not pending:
            break
    return resolved


def _resolve_apkpure_slug(app_name: str, config: dict) -> str | None:
    """
    config['name'] のスラッグで 410 Gone が返った場合、パッケージIDを使って
    APKPure の検索APIからスラッグを再解決する。
    成功時は正しいスラッグ文字列を返す。失敗時は None を返す。
    """
    package = config.get('package', '')
    if not package:
        return None

    search_url = f"{SITE_BASE_URL}/search?q={package}"
    try:
        resp = utils.cf_aware_get(search_url, headers=HEADERS, timeout=TIMEOUT)
        if resp.status_code != 200:
            logging.debug(f"APKPure slug search returned {resp.status_code} for {app_name}")
            return None
        soup = BeautifulSoup(resp.content, "html.parser")
        for a in soup.find_all('a', href=True):
            href = a['href']
            parts = href.strip('/').split('/')
            if len(parts) == 2 and parts[1] == package:
                slug = parts[0]
                logging.info(f"Resolved APKPure slug for {app_name}: {slug}")
                return slug
    except Exception as e:
        logging.debug(f"APKPure slug resolution failed for {app_name}: {e}")
    return None


def _direct_endpoint(
    package: str,
    archive_type: str = "APK",
    **query: str,
) -> str:
    return f"{DOWNLOAD_BASE_URL}/{archive_type}/{package}?{urlencode(query)}"


def _probe_direct_endpoint(url: str) -> str:
    """Resolve a direct APK endpoint and return its filename using four bytes."""
    response = utils.cf_aware_get(
        url,
        headers={**HEADERS, "Range": "bytes=0-3"},
        timeout=30,
        retries=2,
    )
    try:
        response.raise_for_status()
        content_type = response.headers.get("content-type", "").casefold()
        if "html" in content_type or response.content[:2] != b"PK":
            raise ValueError("APKPure direct endpoint did not return an APK archive")
        return utils.extract_filename(response).replace("+", " ")
    finally:
        response.close()


def _latest_direct_version(package: str) -> str | None:
    errors: list[str] = []
    for archive_type in ("APK", "XAPK"):
        try:
            filename = _probe_direct_endpoint(
                _direct_endpoint(package, archive_type, version="latest")
            )
            match = re.search(
                r"_([0-9][^_]+)_APKPure\.(?:apk|xapk)$",
                filename,
                flags=re.IGNORECASE,
            )
            if match:
                return match.group(1).strip()
        except Exception as error:
            errors.append(f"{archive_type}: {error}")
    logging.debug(
        "APKPure direct latest probe failed for %s: %s",
        package,
        "; ".join(errors),
    )
    return None


def get_latest_version(app_name: str, config: dict) -> str | None:
    direct_version = _latest_direct_version(config["package"])
    if direct_version:
        logging.info(
            "APKPure direct endpoint reports %s for %s",
            direct_version,
            app_name,
        )
        return direct_version

    url = f"{SITE_BASE_URL}/{config['name']}/{config['package']}/versions"

    try:
        response = utils.cf_aware_get(url, headers=HEADERS, timeout=TIMEOUT)

        if response.status_code == 410:
            logging.warning(
                f"APKPure returned 410 for {app_name} (slug: {config['name']}). "
                "Attempting slug re-resolution via package ID."
            )
            new_slug = _resolve_apkpure_slug(app_name, config)
            if new_slug:
                url = f"{SITE_BASE_URL}/{new_slug}/{config['package']}/versions"
                response = utils.cf_aware_get(url, headers=HEADERS, timeout=TIMEOUT)
            else:
                logging.warning(f"Could not resolve new APKPure slug for {app_name}. "
                                "Update apps/apkpure/{app_name}.json with the correct name.")
                return None

        response.raise_for_status()

        content_size = len(response.content)
        logging.info(f"URL:{response.url} [{content_size}/{content_size}] -> \"-\" [1]")

        soup = BeautifulSoup(response.content, "html.parser")
        version_info = soup.find('div', class_='ver-top-down')

        if version_info and 'data-dt-version' in version_info.attrs:
            return version_info['data-dt-version']

    except Exception as e:
        logging.error(f"Failed to fetch latest version for {app_name}: {e}")

    return None


def _direct_download_for_candidate(
    candidate: VersionCandidate, app_name: str, config: dict
) -> str | None:
    package = config["package"]
    query = (
        {"versionCode": candidate.code}
        if candidate.code
        else {"version": "latest"}
    )
    errors: list[str] = []
    for archive_type in ("APK", "XAPK"):
        url = _direct_endpoint(package, archive_type, **query)
        try:
            filename = _probe_direct_endpoint(url)
            filename_casefold = filename.casefold()
            aliases_in_filename = any(
                alias.casefold() in filename_casefold
                for alias in candidate.aliases("apkpure")
            )
            # APKPure's versionCode endpoint commonly returns a filename that
            # contains only versionName (for example 26.08.01), not the numeric
            # versionCode used in the request. Treat either known alias as the
            # same release here; the downloaded manifest is still validated
            # afterwards against package/version/versionCode, so a mislabeled
            # response cannot pass the final provenance guard.
            if not aliases_in_filename:
                errors.append(f"{archive_type}: incompatible filename {filename}")
                continue
            logging.info(
                "APKPure direct %s matched %s for %s: %s",
                archive_type,
                candidate.describe(),
                app_name,
                filename,
            )
            return url
        except Exception as error:
            errors.append(f"{archive_type}: {error}")
    logging.info(
        "APKPure direct endpoints failed for %s %s: %s",
        app_name,
        candidate.describe(),
        "; ".join(errors),
    )
    return None


def get_download_link_for_candidate(
    candidate: VersionCandidate, app_name: str, config: dict
) -> str | None:
    if not candidate.code:
        code = discovered_version_code(config["package"], candidate.name)
        if code:
            candidate = VersionCandidate(
                name=candidate.name,
                code=code,
                raw=candidate.raw,
            )
            logging.info(
                "APKPure reused versionCode %s discovered by an earlier provider",
                code,
            )
    direct = _direct_download_for_candidate(candidate, app_name, config)
    if direct:
        return direct
    for version in candidate.aliases("apkpure"):
        link = _html_download_link(version, app_name, config)
        if link:
            return link
    return None


def _html_download_link(version: str, app_name: str, config: dict) -> str | None:
    url = (
        f"{SITE_BASE_URL}/{config['name']}/{config['package']}"
        f"/download/{version}"
    )

    try:
        response = utils.cf_aware_get(url, headers=HEADERS, timeout=TIMEOUT)
        response.raise_for_status()

        content_size = len(response.content)
        logging.info(f"URL:{response.url} [{content_size}/{content_size}] -> \"-\" [1]")

        soup = BeautifulSoup(response.content, "html.parser")

        download_link = soup.find('a', id='download_link')
        if download_link:
            return download_link['href']

    except Exception as e:
        logging.error(f"Failed to fetch download link for {app_name} v{version}: {e}")

    return None


def get_download_link(
    version: str, app_name: str, config: dict
) -> str | None:
    return get_download_link_for_candidate(
        VersionCandidate(name=version),
        app_name,
        config,
    )
