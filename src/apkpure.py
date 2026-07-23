import logging

from src import utils
from bs4 import BeautifulSoup

# Define a standard browser User-Agent to avoid 403 Forbidden errors
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept-Language': 'en-US,en;q=0.9',
    'Referer': 'https://apkpure.net/'
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

    search_url = f"https://apkpure.net/search?q={package}"
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


def get_latest_version(app_name: str, config: str) -> str:
    url = f"https://apkpure.net/{config['name']}/{config['package']}/versions"

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
                url = f"https://apkpure.net/{new_slug}/{config['package']}/versions"
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


def get_download_link(version: str, app_name: str, config: str) -> str:
    url = f"https://apkpure.net/{config['name']}/{config['package']}/download/{version}"

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
