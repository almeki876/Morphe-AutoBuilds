import logging
import re
from urllib.parse import urlencode

from src import utils
from src.versioning import VersionCandidate, discovered_version_code
from bs4 import BeautifulSoup

SITE_BASE_URL = "https://apkpure.com"
DOWNLOAD_BASE_URL = "https://d.apkpure.net/b"

# curl-cffi supplies a User-Agent matching its TLS browser impersonation.
# Overriding it with an old Chrome version creates a detectable mismatch and
# can itself trigger APKPure's anti-bot 403 response.
HEADERS = {
    'Accept-Language': 'en-US,en;q=0.9',
    'Referer': f'{SITE_BASE_URL}/'
}

# APKPureのリクエストタイムアウト（秒）
# デフォルト無制限だと30秒以上かかる場合があるため短縮
TIMEOUT = 15


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
        # 検索結果の最初のアプリリンクからスラッグを抽出
        # 例: /protonvpn/ch.protonvpn.android → "protonvpn"
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

        # 410 Gone: スラッグが変更された可能性があるためパッケージIDで再検索
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
            if not candidate.code and not any(
                alias.casefold() in filename.casefold()
                for alias in candidate.aliases("apkpure")
            ):
                errors.append(f"{archive_type}: incompatible filename {filename}")
                continue
            logging.info(
                "APKPure direct %s matched %s for %s: %s",
                archive_type,
                candidate.describe(),
                app_name,
                filename,
            )
            # Return the stable package endpoint, not the expiring signed redirect.
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
