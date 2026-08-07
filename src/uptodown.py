import logging
import re
from src import utils
from src.versioning import VersionCandidate, parse_candidate
from bs4 import BeautifulSoup


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


def get_latest_version(app_name: str, config: dict) -> str:
    # Generate all possible Uptodown names
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
                # 410 Gone: このスラッグは恒久的に削除されている。次の候補へ。
                logging.debug(f"✗ Gone (410): {url}")
                continue
            else:
                response.raise_for_status()
        except Exception as e:
            logging.debug(f"Failed for {url}: {str(e)[:50]}...")
            continue
    
    logging.error(f"Could not find Uptodown page for {app_name}")
    return None

def get_download_link(version: str, app_name: str, config: dict) -> str:
    # Generate all possible Uptodown names
    possible_names = generate_possible_uptodown_names(config)
    
    logging.info(f"Searching {len(possible_names)} possible Uptodown names for {app_name} v{version}")
    
    for uptodown_name in possible_names:
        base_url = f"https://{uptodown_name}.en.uptodown.com/android"
        try:
            response = utils.cf_aware_get(f"{base_url}/versions")
            if response.status_code != 200:
                continue
                
            soup = BeautifulSoup(response.content, "html.parser")
            data_code = soup.find('h1', id='detail-app-name')['data-code']

            page = 1
            max_pages = 50
            while page <= max_pages:
                response = utils.cf_aware_get(f"{base_url}/apps/{data_code}/versions/{page}")
                response.raise_for_status()
                version_data = response.json().get('data', [])
                
                if not version_data:
                    break
                    
                for entry in version_data:
                    if _entry_matches(entry, version):
                        version_url_parts = entry["versionURL"]
                        version_url = f"{version_url_parts['url']}/{version_url_parts['extraURL']}/{version_url_parts['versionID']}"
                        version_page = utils.cf_aware_get(version_url)
                        version_page.raise_for_status()
                        soup = BeautifulSoup(version_page.content, "html.parser")
                        
                        button = soup.find('button', id='detail-download-button')
                        if not button:
                            continue
                            
                        onclick = button.get('onclick', '')
                        if onclick and "download-link-deeplink" in onclick:
                            version_url += '-x'
                            version_page = utils.cf_aware_get(version_url)
                            version_page.raise_for_status()
                            soup = BeautifulSoup(version_page.content, "html.parser")
                            button = soup.find('button', id='detail-download-button')
                        
                        if button and 'data-url' in button.attrs:
                            download_url = button['data-url']
                            return f"https://dw.uptodown.com/dwn/{download_url}"
                
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
            link = get_download_link(alias, app_name, config)
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

    # The explicitly configured slug is always tried first.
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
