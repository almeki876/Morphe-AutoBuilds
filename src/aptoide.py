import base64
import html
import json
import logging
import re
from typing import Dict
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from src import utils
from src.versioning import VersionCandidate

BASE_URL = "https://ws75.aptoide.com/api/7/"
_SAFE_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,100}$")
_SAFE_LOCALE_RE = re.compile(r"^[a-z]{2}$")
_APTOIDE_APK_EXTENSIONS = (".apk", ".xapk", ".apkm", ".apks")


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
    country parameter. Some releases are visible on one regional frontend but
    absent from the runner's default region. Try only configured, evidence-
    based country fallbacks and accept a path only when both package and
    versionName match exactly. Repository-wide APK manifest validation remains
    the final authority after download.
    """
    if not isinstance(countries, list):
        return None

    seen: set[str] = set()
    for raw_country in countries[:8]:
        country = str(raw_country).strip().casefold()
        if not _SAFE_LOCALE_RE.fullmatch(country) or country in seen:
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
            path = _safe_aptoide_download_path(file_data.get("path"))
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


def _safe_aptoide_download_path(value: object) -> str | None:
    """Accept only HTTPS APK archive URLs hosted by Aptoide."""
    raw = html.unescape(str(value or "").strip()).replace("\\/", "/")
    if not raw:
        return None
    parsed = urlparse(raw)
    host = (parsed.hostname or "").casefold().rstrip(".")
    if (
        parsed.scheme != "https"
        or (host != "aptoide.com" and not host.endswith(".aptoide.com"))
        or not parsed.path.casefold().endswith(_APTOIDE_APK_EXTENSIONS)
    ):
        return None
    return raw


def _iter_dicts(value: object):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _iter_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from _iter_dicts(child)


def _dict_exact_download_path(
    node: dict,
    package: str,
    version: str,
) -> str | None:
    """Extract one exact release from Aptoide frontend JSON metadata."""
    file_data = node.get("file") if isinstance(node.get("file"), dict) else {}
    package_value = (
        node.get("package")
        or node.get("package_name")
        or node.get("packageName")
        or ""
    )
    if package_value and str(package_value) != package:
        return None

    version_values = [
        node.get("vername"),
        node.get("version"),
        node.get("version_name"),
        node.get("versionName"),
        file_data.get("vername"),
        file_data.get("version"),
        file_data.get("version_name"),
        file_data.get("versionName"),
    ]
    exact = any(
        _normalize_vername(str(value or "")) == version
        for value in version_values
        if value is not None
    )
    if not exact:
        return None

    for raw_path in (file_data.get("path"), node.get("path"), node.get("downloadUrl")):
        path = _safe_aptoide_download_path(raw_path)
        if path:
            return path
    return None


def _json_payloads_from_script(text: str):
    stripped = text.strip()
    if not stripped:
        return
    candidates = [stripped]
    first_object = stripped.find("{")
    last_object = stripped.rfind("}")
    if first_object >= 0 and last_object > first_object:
        candidates.append(stripped[first_object : last_object + 1])
    first_array = stripped.find("[")
    last_array = stripped.rfind("]")
    if first_array >= 0 and last_array > first_array:
        candidates.append(stripped[first_array : last_array + 1])

    seen: set[str] = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        try:
            yield json.loads(candidate)
        except (TypeError, ValueError, json.JSONDecodeError):
            continue


def _public_html_exact_path(content: bytes | str, package: str, version: str) -> str | None:
    """Resolve an exact version from Aptoide's server-rendered frontend data.

    The public website can expose a release that the region-sensitive API omits.
    Prefer structured JSON embedded in the page. A narrowly bounded text fallback
    handles older frontend bundles that serialize the same fields inside a JS
    assignment. The normal manifest validator still proves package/version after
    download.
    """
    if isinstance(content, bytes):
        text = content.decode("utf-8", errors="replace")
    else:
        text = str(content)
    if not text or version not in text:
        return None

    soup = BeautifulSoup(text, "html.parser")
    for script in soup.find_all("script"):
        script_text = script.string or script.get_text("", strip=False)
        if not script_text or version not in script_text:
            continue
        for payload in _json_payloads_from_script(script_text):
            for node in _iter_dicts(payload):
                path = _dict_exact_download_path(node, package, version)
                if path:
                    return path

    # Historical Aptoide frontends serialized app metadata into JavaScript with
    # fields such as "vername" and "path". Keep the match close to the exact
    # requested version so a neighbouring release cannot be selected by name.
    normalized = html.unescape(text).replace("\\/", "/")
    if package not in normalized:
        return None
    quoted_version = re.escape(version)
    pair_patterns = (
        re.compile(
            rf'"(?:vername|version|versionName|version_name)"\s*:\s*"{quoted_version}"'
            rf'.{{0,2500}}?"(?:path|downloadUrl)"\s*:\s*"(https://[^"\\]+)"',
            re.IGNORECASE | re.DOTALL,
        ),
        re.compile(
            rf'"(?:path|downloadUrl)"\s*:\s*"(https://[^"\\]+)"'
            rf'.{{0,2500}}?"(?:vername|version|versionName|version_name)"\s*:\s*"{quoted_version}"',
            re.IGNORECASE | re.DOTALL,
        ),
    )
    for pattern in pair_patterns:
        for match in pattern.finditer(normalized):
            path = _safe_aptoide_download_path(match.group(1))
            if path:
                return path
    return None


def _public_exact_path(package: str, version: str, config: Dict) -> str | None:
    """Try exact public Aptoide regional pages when API history is incomplete."""
    slug = str(config.get("name") or "").strip().casefold()
    if not _SAFE_SLUG_RE.fullmatch(slug):
        return None

    locales: list[str] = []
    for raw_locale in [*(config.get("country_fallbacks") or []), "en"]:
        locale = str(raw_locale).strip().casefold()
        if _SAFE_LOCALE_RE.fullmatch(locale) and locale not in locales:
            locales.append(locale)

    for locale in locales[:9]:
        base_url = f"https://{slug}.{locale}.aptoide.com"
        for suffix in ("/versions", "/app"):
            url = base_url + suffix
            try:
                response = utils.cf_aware_get(url)
                logging.info(
                    "aptoide: public frontend status=%s package=%s locale=%s page=%s",
                    response.status_code,
                    package,
                    locale,
                    suffix.lstrip("/"),
                )
                if response.status_code != 200:
                    continue
                path = _public_html_exact_path(response.content, package, version)
                if path:
                    logging.info(
                        "✓ aptoide: public frontend resolved exact release "
                        "package=%s version=%s locale=%s",
                        package,
                        version,
                        locale,
                    )
                    return path
            except Exception as error:
                logging.info(
                    "aptoide: public frontend lookup failed package=%s locale=%s: %s",
                    package,
                    locale,
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
                path = _safe_aptoide_download_path(file_data.get("path"))
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
    """Use exact versionCode first, then public exact-version metadata."""
    package = config["package"]
    if candidate.code:
        q = _get_q_param(config.get("arch", "universal"))
        error: Exception | None = None
        try:
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
            path = _safe_aptoide_download_path(file_data.get("path"))
            if not path:
                raise ValueError(
                    f"aptoide: no safe download path for version code {candidate.code}"
                )
            return path
        except Exception as exc:
            error = exc
            logging.info(
                "aptoide: exact versionCode API lookup failed for %s %s: %s",
                package,
                candidate.describe(),
                utils.safe_text_for_log(exc),
            )

        # Public history has versionName but not a trustworthy Android
        # versionCode. It may still supply the file; the repository-wide APK
        # identity validator enforces candidate.code after download.
        for alias in candidate.aliases("aptoide"):
            public = _public_exact_path(package, alias, config)
            if public:
                return public
        assert error is not None
        raise error

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

    if store_name:
        url = f"{BASE_URL}getApp?package_name={package}&store_name={store_name}{q}"
        res = utils.cf_aware_get(url)
        res.raise_for_status()
        data = res.json()
        path = _safe_aptoide_download_path(data.get('data', {}).get('file', {}).get('path'))
        if path:
            return path
        raise ValueError(f"aptoide: no safe download path for '{package}' in store '{store_name}'")

    if version.lower() == "latest":
        url = f"{BASE_URL}apps/search?query={package}&limit=10&trusted=true{q}"
        res = utils.cf_aware_get(url)
        res.raise_for_status()
        data = res.json()
        items = data.get('datalist', {}).get('list', [])
        item = _exact_package(items, package)
        if not item:
            raise ValueError(f"aptoide: no exact result for package '{package}'")
        path = _safe_aptoide_download_path(item['file'].get('path'))
        if not path:
            raise ValueError(f"aptoide: no safe download path for package '{package}'")
        return path

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

        public = _public_exact_path(package, version, config)
        if public:
            return public

        logging.warning(
            f"aptoide: version '{version}' not in API/public history for '{package}', "
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
        path = _safe_aptoide_download_path(item['file'].get('path'))
        if not path:
            raise ValueError(f"aptoide: no safe download path for package '{package}'")
        return path

    url_meta = f"{BASE_URL}getAppMeta?package_name={package}&vercode={vercode}{q}"
    res_meta = utils.cf_aware_get(url_meta)
    res_meta.raise_for_status()
    path = _safe_aptoide_download_path(res_meta.json()['data']['file'].get('path'))
    if not path:
        raise ValueError(
            f"aptoide: getAppMeta returned no safe path for {package}@{vercode}"
        )
    return path


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
