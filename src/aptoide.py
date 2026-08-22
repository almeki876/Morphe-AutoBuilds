import base64
import logging
import re
from typing import Dict
from src import utils
from src.versioning import VersionCandidate

BASE_URL = "https://ws75.aptoide.com/api/7/"


def _exact_package(items: list, package: str) -> dict | None:
    """Never accept a fuzzy search result for a different Android package."""
    for item in items:
        candidate = item.get("package") or item.get("package_name")
        if candidate == package:
            return item
    return None


def _app_get_data(payload: object) -> dict:
    """Read app metadata from both current and older Aptoide response shapes."""
    if not isinstance(payload, dict):
        return {}
    nodes = payload.get("nodes")
    if isinstance(nodes, dict):
        meta = nodes.get("meta")
        if isinstance(meta, dict) and isinstance(meta.get("data"), dict):
            return meta["data"]
    data = payload.get("data")
    return data if isinstance(data, dict) else {}


def _regional_exact_path(
    package: str,
    version: str,
    q: str,
    countries: object,
) -> str | None:
    """Resolve a georestricted exact release through Aptoide's official app/get.

    Aptoide documents that app/get is georestricted and supports an explicit
    country parameter.  Some releases are visible on one regional frontend but
    absent from the runner's default region.  Try only configured, evidence-
    based country fallbacks and accept a path only when both package and
    versionName match exactly.  Repository-wide APK manifest validation remains
    the final authority after download.
    """
    if not isinstance(countries, list):
        return None

    seen: set[str] = set()
    for raw_country in countries[:8]:
        country = str(raw_country).strip().casefold()
        if not re.fullmatch(r"[a-z]{2}", country) or country in seen:
            continue
        seen.add(country)
        url = (
            f"{BASE_URL}app/get?package_name={package}"
            f"&nodes=meta&country={country}{q}"
        )
        try:
            response = utils.cf_aware_get(url)
            logging.info(
                "aptoide: regional app/get status=%s package=%s country=%s",
                response.status_code,
                package,
                country,
            )
            if response.status_code != 200:
                continue
            app = _app_get_data(response.json())
            returned_package = str(app.get("package", ""))
            file_data = app.get("file", {}) if isinstance(app, dict) else {}
            if not isinstance(file_data, dict):
                continue
            returned_name = _normalize_vername(str(file_data.get("vername", "")))
            if returned_package != package or returned_name != version:
                logging.info(
                    "aptoide: regional result did not match exact target "
                    "country=%s package_match=%s version=%s",
                    country,
                    returned_package == package,
                    returned_name or "none",
                )
                continue
            path = str(file_data.get("path", "")).strip()
            if path:
                logging.info(
                    "✓ aptoide: regional exact release found package=%s "
                    "version=%s country=%s",
                    package,
                    version,
                    country,
                )
                return path
        except Exception as error:
            logging.info(
                "aptoide: regional lookup failed package=%s country=%s: %s",
                package,
                country,
                utils.safe_text_for_log(error),
            )
    return None


def _find_version(package: str, version: str, q: str) -> tuple[str | None, str | None]:
    """Find an exact historical release without truncating Aptoide history.

    Aptoide list endpoints expose ``datalist.next`` for pagination. Some apps
    have more than 100 historical releases, so reading only the first page can
    incorrectly report a still-hosted older version as missing. Keep the scan
    bounded and stop on empty/repeated pages to avoid looping on malformed API
    responses.
    """
    offset = 0
    seen_offsets: set[int] = set()
    max_pages = 20

    for _ in range(max_pages):
        if offset in seen_offsets:
            break
        seen_offsets.add(offset)
        url = (
            f"{BASE_URL}listAppVersions?package_name={package}"
            f"&limit=100&offset={offset}{q}"
        )
        response = utils.cf_aware_get(url)
        response.raise_for_status()
        datalist = response.json().get("datalist", {})
        items = datalist.get("list", []) if isinstance(datalist, dict) else []
        if not isinstance(items, list) or not items:
            break

        for app in items:
            if not isinstance(app, dict):
                continue
            file_data = app.get("file", {})
            found_name = _normalize_vername(str(file_data.get("vername", "")))
            if found_name == version:
                code = str(file_data.get("vercode", "")).strip() or None
                path = str(file_data.get("path", "")).strip() or None
                return code, path

        next_offset = datalist.get("next") if isinstance(datalist, dict) else None
        try:
            next_offset = int(next_offset)
        except (TypeError, ValueError):
            next_offset = offset + len(items)
        if next_offset <= offset or len(items) < 100:
            break
        offset = next_offset

    return None, None


def get_latest_version(app_name: str, config: Dict) -> str:
    package = config['package']
    arch = config.get('arch', 'universal')
    q = _get_q_param(arch)

    # If a specific store_name is configured, use getApp endpoint directly
    store_name = config.get('store_name')
    if store_name:
        url = f"{BASE_URL}getApp?package_name={package}&store_name={store_name}{q}"
        res = utils.cf_aware_get(url)
        res.raise_for_status()
        data = res.json()
        app_data = data.get('data', {})
        version = app_data.get('file', {}).get('vername')
        if version:
            version = _normalize_vername(version)
            logging.info(f"aptoide: found version {version} for {package} (store: {store_name})")
            return version
        raise ValueError(f"aptoide: could not get version for '{package}' from store '{store_name}'")

    url = f"{BASE_URL}apps/search?query={package}&limit=10&trusted=true{q}"
    res = utils.cf_aware_get(url)
    res.raise_for_status()
    data = res.json()
    items = data.get('datalist', {}).get('list', [])
    item = _exact_package(items, package)
    if not item:
        raise ValueError(
            f"aptoide: no exact result for package '{package}' "
            "(app may not exist on Aptoide)"
        )
    version = _normalize_vername(item['file']['vername'])
    logging.info(f"aptoide: found version {version} for {package}")
    return version


def get_download_link_for_candidate(
    candidate: VersionCandidate, app_name: str, config: Dict
) -> str:
    """Use Aptoide's version-code endpoint before slower version-name lookup."""
    if candidate.code:
        package = config["package"]
        q = _get_q_param(config.get("arch", "universal"))
        url = (
            f"{BASE_URL}getAppMeta?package_name={package}"
            f"&vercode={candidate.code}{q}"
        )
        response = utils.cf_aware_get(url)
        response.raise_for_status()
        data = response.json().get("data", {})
        file_data = data.get("file", {})
        returned_code = str(file_data.get("vercode", ""))
        returned_name = _normalize_vername(str(file_data.get("vername", "")))
        if returned_code != candidate.code:
            raise ValueError(
                f"aptoide: requested version code {candidate.code}, "
                f"received {returned_code or 'none'}"
            )
        if returned_name not in candidate.aliases("aptoide"):
            raise ValueError(
                f"aptoide: version code {candidate.code} resolved to "
                f"{returned_name!r}, expected {candidate.name!r}"
            )
        path = file_data.get("path")
        if not path:
            raise ValueError(
                f"aptoide: no download path for version code {candidate.code}"
            )
        return path

    errors: list[str] = []
    for alias in candidate.aliases("aptoide"):
        try:
            return get_download_link(alias, app_name, config)
        except Exception as error:
            errors.append(f"{alias}: {type(error).__name__}: {error}")
    raise ValueError("; ".join(errors))


def get_download_link(version: str, app_name: str, config: Dict) -> str:
    package = config['package']
    arch = config.get('arch', 'universal')
    q = _get_q_param(arch)
    store_name = config.get('store_name')

    # If a specific store_name is configured, use getApp endpoint directly
    if store_name:
        url = f"{BASE_URL}getApp?package_name={package}&store_name={store_name}{q}"
        res = utils.cf_aware_get(url)
        res.raise_for_status()
        data = res.json()
        path = data.get('data', {}).get('file', {}).get('path')
        if path:
            return path
        raise ValueError(f"aptoide: no download path for '{package}' in store '{store_name}'")

    if version.lower() == "latest":
        url = f"{BASE_URL}apps/search?query={package}&limit=10&trusted=true{q}"
        res = utils.cf_aware_get(url)
        res.raise_for_status()
        data = res.json()
        items = data.get('datalist', {}).get('list', [])
        item = _exact_package(items, package)
        if not item:
            raise ValueError(f"aptoide: no exact result for package '{package}'")
        return item['file']['path']

    # Search all available history pages for an exact version. A direct path on
    # the version row can be used immediately; otherwise resolve the exact
    # versionCode through getAppMeta as before.
    vercode, path = _find_version(package, version, q)
    if path:
        return path
    if not vercode:
        regional = _regional_exact_path(
            package,
            version,
            q,
            config.get("country_fallbacks"),
        )
        if regional:
            return regional

        # Version not found in listAppVersions — fall back to search API.
        # Only use the result if it matches the requested package/version exactly.
        logging.warning(
            f"aptoide: version '{version}' not in listAppVersions for '{package}', "
            f"falling back to search API"
        )
        url_search = f"{BASE_URL}apps/search?query={package}&limit=10&trusted=true{q}"
        res_s = utils.cf_aware_get(url_search)
        res_s.raise_for_status()
        items = res_s.json().get('datalist', {}).get('list', [])
        item = _exact_package(items, package)
        if not item:
            raise ValueError(f"aptoide: version '{version}' not found for package '{package}'")
        found_vername = item['file'].get('vername', '')
        normalized = _normalize_vername(found_vername)
        if normalized != version:
            raise ValueError(
                f"aptoide: version '{version}' not available for package '{package}' "
                f"(search returned '{found_vername}' instead)"
            )
        path = item['file'].get('path')
        if not path:
            raise ValueError(f"aptoide: no download path for package '{package}'")
        return path

    url_meta = f"{BASE_URL}getAppMeta?package_name={package}&vercode={vercode}{q}"
    res_meta = utils.cf_aware_get(url_meta)
    res_meta.raise_for_status()
    return res_meta.json()['data']['file']['path']


def _normalize_vername(vername: str) -> str:
    """Normalize Aptoide vername for comparison.

    Aptoide's search API sometimes returns vername in the format
    "87100 (8.7.1)" where the first token is a version code and the
    parenthesised part is the human-readable version string. Strip the
    leading vercode token so the result can be compared against a plain
    semantic version like "8.7.1".
    """
    m = re.search(r'\(([^)]+)\)\s*$', vername)
    if m:
        return m.group(1)
    return vername


def _get_q_param(arch: str) -> str:
    if arch == 'universal':
        return ''
    cpu_map = {
        'arm64-v8a': 'arm64-v8a,armeabi-v7a,armeabi',
        'armeabi-v7a': 'armeabi-v7a,armeabi',
    }
    cpu = cpu_map.get(arch, '')
    if cpu:
        q_str = f"myCPU={cpu}&leanback=0"
        encoded = (
            base64.urlsafe_b64encode(q_str.encode("utf-8"))
            .decode("ascii")
            .rstrip("=")
        )
        return f"&q={encoded}"
    return ''
