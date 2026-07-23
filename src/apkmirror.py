import re
import json
import logging
from bs4 import BeautifulSoup
from urllib.parse import quote
from src import utils

base_url = "https://www.apkmirror.com"

def get_build_number_for_version(version: str, config: dict) -> tuple[str | None, str]:
    """Fetch build number for a specific version from APKMirror.
    Returns (build_number, format_type) where format_type is 'parentheses' or 'build_suffix'.
    Returns the LOWEST build number found, since patches are typically made for initial builds."""
    try:
        main_url = f"{base_url}/apk/{config['org']}/{config['name']}/"
        response = utils.cf_aware_get(main_url)
        if response.status_code == 200:
            soup = BeautifulSoup(response.content, "html.parser")
            # Collect all build numbers for this version
            builds_found = []
            for link in soup.find_all('a', href=True):
                text = link.get_text()
                if version in text:
                    # Format 1: "32.30.0(1575420)" -> parentheses
                    build_match = re.search(rf'{re.escape(version)}\((\d+)\)', text)
                    if build_match:
                        builds_found.append((build_match.group(1), 'parentheses'))
                    # Format 2: "6.6 build 006" -> build suffix
                    build_match = re.search(rf'{re.escape(version)}\s+build\s+(\d+)', text, re.IGNORECASE)
                    if build_match:
                        builds_found.append((build_match.group(1), 'build_suffix'))
            
            # Return the lowest build number (patches are typically for initial builds)
            if builds_found:
                # Sort by build number (as integer) and return the lowest
                builds_found.sort(key=lambda x: int(x[0]))
                return builds_found[0]
    except Exception as e:
        logging.debug(f"Could not fetch build number: {e}")
    return None, None

def get_download_link(version: str, app_name: str, config: dict, arch: str = None) -> str: 
    target_arch = arch if arch else config.get('arch', 'universal')
    
    criteria = [config['type'], target_arch, config['dpi']]
    
    # --- UNIVERSAL URL FINDER WITH VALIDATION ---
    # Extract build number if present (e.g., "32.30.0(1575420)" -> version="32.30.0", build="1575420")
    build_number = None
    build_format = None
    
    # Check for parentheses format: "32.30.0(1575420)"
    build_match = re.search(r'\((\d+)\)$', version)
    if build_match:
        build_number = build_match.group(1)
        build_format = 'parentheses'
        version = version[:build_match.start()]
    else:
        # Check for build suffix format: "6.6 build 002"
        build_match = re.search(r'\s+build\s+(\d+)$', version, re.IGNORECASE)
        if build_match:
            build_number = build_match.group(1)
            build_format = 'build_suffix'
            version = version[:build_match.start()]
        else:
            # Try to fetch build number from APKMirror for this version
            build_number, build_format = get_build_number_for_version(version, config)
            if build_number:
                logging.info(f"Found build number {build_number} for version {version} (format: {build_format})")
    
    version_parts = version.split('.')
    found_soup = None
    correct_version_page = False
    
    # Use release_prefix if available, otherwise use app name
    release_name = config.get('release_prefix', config['name'])
    
    # Loop backwards: Try full version, then strip parts
    for i in range(len(version_parts), 0, -1):
        current_ver_str = "-".join(version_parts[:i])
        
        # If build number exists, append it to the last version part in URL
        if build_number and i == len(version_parts):
            if build_format == 'build_suffix':
                # e.g., "6-6" + "build-006" -> "6-6-build-006"
                current_ver_str = current_ver_str + "-build-" + build_number
            else:
                # e.g., "32-30-0" + "1575420" -> "32-30-01575420"
                parts = version_parts[:i]
                parts[-1] = parts[-1] + build_number
                current_ver_str = "-".join(parts)
        
        # Generate ALL possible URL patterns in priority order
        url_patterns = []
        
        # URL-encode the release_name to handle unicode characters like ․
        encoded_release_name = quote(release_name, safe='')
        encoded_name = quote(config['name'], safe='')
        
        # Priority 1: With release_name and -release suffix (most specific)
        url_patterns.append(f"{base_url}/apk/{config['org']}/{encoded_name}/{encoded_release_name}-{current_ver_str}-release/")
        
        # Priority 2: With app name and -release suffix
        if release_name != config['name']:
            url_patterns.append(f"{base_url}/apk/{config['org']}/{encoded_name}/{encoded_name}-{current_ver_str}-release/")
        
        # Priority 3: With release_name without -release
        url_patterns.append(f"{base_url}/apk/{config['org']}/{encoded_name}/{encoded_release_name}-{current_ver_str}/")
        
        # Priority 4: With app name without -release
        if release_name != config['name']:
            url_patterns.append(f"{base_url}/apk/{config['org']}/{encoded_name}/{encoded_name}-{current_ver_str}/")
        
        # Remove duplicate patterns
        url_patterns = list(dict.fromkeys(url_patterns))
        
        for url in url_patterns:
            logging.info(f"Checking potential release URL: {url}")
            
            try:
                response = utils.cf_aware_get(url)
                if response.status_code == 200:
                    soup = BeautifulSoup(response.content, "html.parser")
                    page_text = soup.get_text()
                    
                    # VALIDATION: Check if this page is for our EXACT version
                    # Check multiple possible version formats
                    version_checks = [
                        version,  # 6.6
                        version.replace('.', '-'),  # 6-6
                        current_ver_str,  # 6-6-build-002 (if stripped)
                        ".".join(version_parts[:i])  # 6.6 (if stripped)
                    ]
                    
                    # Add build suffix format if we have a build number
                    if build_number:
                        if build_format == 'build_suffix':
                            version_checks.append(f"{version} build {build_number}")  # 6.6 build 002
                            version_checks.append(f"{version.replace('.', '-')}-build-{build_number}")  # 6-6-build-002
                        else:
                            version_checks.append(f"{version}({build_number})")  # 32.30.0(1575420)
                    
                    # Also check page title and headings for version
                    title_tag = soup.find('title')
                    headings = soup.find_all(['h1', 'h2', 'h3'])
                    
                    is_correct_page = False
                    
                    # Check in page text
                    for check in version_checks:
                        if check and check in page_text:
                            # Accept version match if it's the base version or includes build info
                            if check == version or check == version.replace('.', '-') or check == current_ver_str:
                                is_correct_page = True
                                break
                    
                    # Check in title and headings
                    if not is_correct_page:
                        for heading in headings:
                            heading_text = heading.get_text()
                            for check in version_checks:
                                if check and check in heading_text:
                                    is_correct_page = True
                                    break
                            if is_correct_page:
                                break
                    
                    if not is_correct_page and title_tag:
                        title_text = title_tag.get_text()
                        for check in version_checks:
                            if check and check in title_text:
                                is_correct_page = True
                                break
                    
                    if is_correct_page:
                        content_size = len(response.content)
                        logging.info(f"✓ Correct version page found: {response.url}")
                        found_soup = soup
                        correct_version_page = True
                        break  # Found correct page!
                    else:
                        # Page exists but doesn't have our version as primary
                        logging.warning(f"Page found but not for version {version}: {url}")
                        # Save as fallback ONLY if we haven't found any page yet
                        if found_soup is None:
                            found_soup = soup
                            logging.warning(f"Saved as fallback page (may list multiple versions)")
                        continue
                        
                elif response.status_code == 404:
                    logging.info(f"URL not found (404): {url}")
                    continue
                else:
                    logging.warning(f"URL {url} returned status {response.status_code}")
                    continue
                    
            except Exception as e:
                logging.warning(f"Error checking {url}: {str(e)[:50]}")
                continue
        
        if correct_version_page:
            break  # Found correct page for this version part
    
    # If we didn't find the exact version page but found a fallback
    if not correct_version_page and found_soup:
        logging.warning(f"Using fallback page for {app_name} {version} (may contain multiple versions)")
    
    if not found_soup:
        logging.error(f"Could not find any release page for {app_name} {version}")
        return None
    
    # --- VARIANT FINDER (works with both exact pages and fallback pages) ---
    rows = found_soup.find_all('div', class_='table-row headerFont')
    download_page_url = None
    
    # Try to find exact version match first
    for row in rows:
        row_text = row.get_text()
        
        # Check if row contains our exact version
        if version in row_text or version.replace('.', '-') in row_text:
            # Check criteria
            if all(criterion in row_text for criterion in criteria):
                sub_url = row.find('a', class_='accent_color')
                if sub_url:
                    download_page_url = base_url + sub_url['href']
                    break
    
    # If exact version not found, try to find any variant matching criteria
    if not download_page_url:
        for row in rows:
            row_text = row.get_text()
            if all(criterion in row_text for criterion in criteria):
                # Check if this looks like a variant row (has version numbers)
                if re.search(r'\d+(\.\d+)+', row_text):
                    sub_url = row.find('a', class_='accent_color')
                    if sub_url:
                        download_page_url = base_url + sub_url['href']
                        # Extract version for logging
                        match = re.search(r'(\d+(\.\d+)+(\.\w+)*)', row_text)
                        if match:
                            actual_version = match.group(1)
                            logging.warning(f"Using variant {actual_version} (criteria match)")
                        break
    
    if not download_page_url:
        logging.error(f"No variant found for {app_name} {version} with criteria {criteria}")
        # Debug: log what rows we found
        logging.debug(f"Found {len(rows)} rows total")
        for idx, row in enumerate(rows[:5]):  # First 5 rows
            logging.debug(f"Row {idx}: {row.get_text()[:100]}...")
        
        # Fallback: try relaxed criteria (drop arch requirement, accept APKM/bundle types)
        fallback_criteria_sets = [
            [config['type'], 'universal', config['dpi']],  # try universal arch
            [config['type'], config['dpi']],               # drop arch entirely
            ['BUNDLE', config['dpi']],                     # try bundle type
            [config['dpi']],                               # only dpi requirement
        ]
        for fallback_criteria in fallback_criteria_sets:
            for row in rows:
                row_text = row.get_text()
                if all(criterion in row_text for criterion in fallback_criteria):
                    if re.search(r'\d+(\.\d+)+', row_text):
                        sub_url = row.find('a', class_='accent_color')
                        if sub_url:
                            download_page_url = base_url + sub_url['href']
                            logging.warning(f"Using fallback variant with relaxed criteria {fallback_criteria}")
                            break
            if download_page_url:
                break
        
        if not download_page_url:
            return None
    
    # --- STANDARD DOWNLOAD FLOW ---
    try:
        headers_variant = {
            "Referer": base_url + "/",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }
        response = utils.cf_aware_get(download_page_url, headers=headers_variant)
        response.raise_for_status()
        content_size = len(response.content)
        logging.info(f"URL:{response.url} [{content_size}/{content_size}] -> Variant Page")
        soup = BeautifulSoup(response.content, "html.parser")

        sub_url = soup.find('a', class_='downloadButton')
        if sub_url:
            final_download_page_url = base_url + sub_url['href']
            headers_dl_page = {
                "Referer": download_page_url,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            }
            response = utils.cf_aware_get(final_download_page_url, headers=headers_dl_page)
            response.raise_for_status()
            content_size = len(response.content)
            logging.info(f"URL:{response.url} [{content_size}/{content_size}] -> Download Page")
            soup = BeautifulSoup(response.content, "html.parser")

            button = soup.find('a', id='download-link')
            if button:
                href = button['href']
                # href が絶対URLの場合はそのまま、相対パスの場合は base_url を付与
                if href.startswith('http'):
                    return href
                return base_url + href
    except Exception as e:
        logging.error(f"Error in download flow: {e}")
    
    return None

def get_architecture_criteria(arch: str) -> dict:
    """Map architecture names to APKMirror criteria"""
    arch_mapping = {
        "arm64-v8a": "arm64-v8a",
        "armeabi-v7a": "armeabi-v7a", 
        "universal": "universal"
    }
    return arch_mapping.get(arch, "universal")
    
def get_latest_version(app_name: str, config: dict) -> str:
    # First try: get from main app page
    try:
        main_url = f"{base_url}/apk/{config['org']}/{config['name']}/"
        response = utils.cf_aware_get(main_url)
        if response.status_code == 200:
            soup = BeautifulSoup(response.content, "html.parser")
            # Try to find version in the page
            version_elem = soup.find('span', string=re.compile(r'\d+\.\d+'))
            if version_elem:
                version_text = version_elem.text.strip()
                match = re.search(r'(\d+(\.\d+)+)', version_text)
                if match:
                    return match.group(1)
    except:
        pass  # If fails, continue to original method
    
    # Original method (keep exactly as you had it)
    url = f"{base_url}/uploads/?appcategory={config['name']}"
    
    response = utils.cf_aware_get(url)
    response.raise_for_status()
    content_size = len(response.content)
    logging.info(f"URL:{response.url} [{content_size}/{content_size}] -> \"-\" [1]")
    soup = BeautifulSoup(response.content, "html.parser")

    app_rows = soup.find_all("div", class_="appRow")
    version_pattern = re.compile(r'\d+(\.\d+)*(-[a-zA-Z0-9]+(\.\d+)*)*')

    for row in app_rows:
        version_text = row.find("h5", class_="appRowTitle").a.text.strip()
        if "alpha" not in version_text.lower() and "beta" not in version_text.lower():
            match = version_pattern.search(version_text)
            if match:
                version = match.group()
                version_parts = version.split('.')
                base_version_parts = []
                for part in version_parts:
                    if part.isdigit():
                        base_version_parts.append(part)
                    else:
                        break
                if base_version_parts:
                    base_version = '.'.join(base_version_parts)
                    
                    # Check for build number in parentheses like "32.30.0(1575420)"
                    build_match = re.search(r'\((\d+)\)', version_text)
                    if build_match:
                        build_number = build_match.group(1)
                        return f"{base_version}({build_number})"
                    
                    return base_version

    return None
