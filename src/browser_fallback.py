"""Last-resort browser discovery for mirror pages that require JavaScript.

This module deliberately does not solve CAPTCHAs or weaken APK identity checks.
It only renders normal public pages with the Chrome/ChromeDriver already present
on GitHub's Ubuntu runner, discovers a concrete download URL, and returns it to
the normal downloader. The caller must still validate the downloaded manifest.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin, urlparse

from src import providers, utils
from src.versioning import VersionCandidate


@dataclass(frozen=True)
class BrowserDownload:
    url: str
    headers: dict[str, str]
    source: str


class BrowserFallbackError(RuntimeError):
    pass


_CHALLENGE_MARKERS = (
    "just a moment",
    "verify you are human",
    "checking your browser",
    "cf-chl-",
    "challenges.cloudflare.com",
    "recaptcha",
    "hcaptcha",
)
_DIRECT_IN_ONCLICK_RE = re.compile(
    r"(?:https?:)?//dw\.uptodown\.com/dwn/[A-Za-z0-9._~!$&'()*+,;=:@%/-]+"
    r"|/dwn/[A-Za-z0-9._~!$&'()*+,;=:@%/-]+",
    re.IGNORECASE,
)
_SAFE_CARD_COMPONENT_RE = re.compile(r"^[A-Za-z0-9._~-]+$")
_SAFE_EXTRA_PATH_RE = re.compile(r"^[A-Za-z0-9._~/-]+$")


def _driver_paths() -> tuple[str | None, str | None]:
    """Use runner-provided binaries; never download a driver at runtime."""
    chrome = (
        shutil.which("google-chrome")
        or shutil.which("google-chrome-stable")
        or shutil.which("chromium")
        or shutil.which("chromium-browser")
    )
    driver = shutil.which("chromedriver")
    driver_root = os.getenv("CHROMEWEBDRIVER", "").strip()
    if not driver and driver_root:
        candidate = Path(driver_root)
        if candidate.is_dir():
            candidate = candidate / "chromedriver"
        if candidate.is_file():
            driver = str(candidate)
    return chrome, driver


def _challenge_present(title: str, html: str) -> bool:
    sample = f"{title}\n{html[:131072]}".casefold()
    return any(marker in sample for marker in _CHALLENGE_MARKERS)


def _safe_download_url(raw: str, page_url: str) -> str | None:
    value = (raw or "").strip()
    if not value:
        return None
    if value.startswith("//"):
        return "https:" + value
    if value.startswith(("http://", "https://")):
        return value
    if value.startswith("/dwn/"):
        return "https://dw.uptodown.com" + value
    if "/" not in value and len(value) > 20:
        return f"https://dw.uptodown.com/dwn/{value}"
    return urljoin(page_url, value)


def _is_uptodown_host(hostname: str | None) -> bool:
    host = (hostname or "").casefold().rstrip(".")
    return host == "uptodown.com" or host.endswith(".uptodown.com")


def _is_direct_uptodown_file_url(url: str | None) -> bool:
    if not url:
        return False
    parsed = urlparse(url)
    return (
        parsed.scheme == "https"
        and (parsed.hostname or "").casefold() == "dw.uptodown.com"
        and parsed.path.startswith("/dwn/")
        and len(parsed.path) > len("/dwn/")
    )


def _is_safe_uptodown_page_url(url: str | None) -> bool:
    if not url:
        return False
    parsed = urlparse(url)
    return parsed.scheme == "https" and _is_uptodown_host(parsed.hostname)


def _is_concrete_uptodown_release_url(url: str | None) -> bool:
    """Recognize a history card URL that already identifies one release."""
    if not _is_safe_uptodown_page_url(url):
        return False
    path = urlparse(str(url)).path.rstrip("/")
    return bool(
        re.search(r"/android/(?:download|post-download)/[^/]+$", path)
    )


def _direct_url_from_target(target: dict, page_url: str) -> str | None:
    """Return only a concrete Uptodown CDN object, never an HTML download page."""
    for key in ("dataUrl", "href"):
        candidate = _safe_download_url(str(target.get(key) or ""), page_url)
        if _is_direct_uptodown_file_url(candidate):
            return candidate

    onclick = str(target.get("onclick") or "")
    match = _DIRECT_IN_ONCLICK_RE.search(onclick)
    if match:
        candidate = _safe_download_url(match.group(0), page_url)
        if _is_direct_uptodown_file_url(candidate):
            return candidate
    return None


def _version_target_page_url(target: dict, versions_url: str) -> str | None:
    """Build the exact rendered history-card release page when metadata exists."""
    version_id = str(target.get("dataVersionId") or "").strip()
    if version_id:
        if not _SAFE_CARD_COMPONENT_RE.fullmatch(version_id):
            return None
        raw_root = str(target.get("dataUrl") or "").strip()
        if raw_root:
            root = _safe_download_url(raw_root, versions_url)
            if _is_concrete_uptodown_release_url(root):
                return root
        else:
            root = versions_url.rsplit("/versions", 1)[0]
        if not _is_safe_uptodown_page_url(root):
            return None
        extra_url = str(target.get("dataExtraUrl") or "download").strip(" /")
        if not extra_url or not _SAFE_EXTRA_PATH_RE.fullmatch(extra_url):
            return None
        return f"{str(root).rstrip('/')}/{extra_url}/{version_id}"

    href = _safe_download_url(str(target.get("href") or ""), versions_url)
    return href if _is_safe_uptodown_page_url(href) else None


def _cookies_as_header(driver) -> dict[str, str]:
    cookies = []
    for cookie in driver.get_cookies():
        name = str(cookie.get("name", "")).strip()
        value = str(cookie.get("value", "")).strip()
        if name and value:
            cookies.append(f"{name}={value}")
    headers = {
        "Referer": driver.current_url,
        "User-Agent": driver.execute_script("return navigator.userAgent"),
    }
    if cookies:
        headers["Cookie"] = "; ".join(cookies)
    return headers


def _wait_for_normal_page(driver, timeout: float = 12.0) -> None:
    """Allow ordinary JS/interstitial rendering, but never solve a CAPTCHA."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        html = driver.page_source or ""
        if not _challenge_present(driver.title or "", html):
            return
        time.sleep(1.0)
    raise BrowserFallbackError(
        f"interactive browser challenge persisted at {utils.safe_url_for_log(driver.current_url)}"
    )


def _find_version_target(driver, aliases: tuple[str, ...]):
    """Find a version card without depending on Uptodown CSS class names."""
    script = r"""
const aliases = arguments[0].map(v => String(v).trim().toLowerCase()).filter(Boolean);
const nodes = Array.from(document.querySelectorAll('a,button,[data-url],[data-version-id],div,span,li,article'));
function normalized(el) { return (el.innerText || el.textContent || '').trim().toLowerCase(); }
function matches(text) {
  return aliases.some(v => text === v || text.startsWith(v + ' ') || text.includes(' ' + v + ' '));
}
for (const el of nodes) {
  const text = normalized(el);
  if (!text || !matches(text)) continue;
  let cur = el;
  let action = null;
  for (let depth = 0; cur && depth < 8; depth++, cur = cur.parentElement) {
    const versionId = cur.getAttribute && cur.getAttribute('data-version-id');
    if (versionId) {
      return {
        dataVersionId: versionId || '',
        dataExtraUrl: cur.getAttribute('data-extra-url') || '',
        dataUrl: cur.getAttribute('data-url') || '',
        href: cur.href || cur.getAttribute('href') || '',
        onclick: cur.getAttribute('onclick') || '',
        tag: cur.tagName || '',
        text: normalized(cur).slice(0, 300)
      };
    }
    const dataUrl = cur.getAttribute && cur.getAttribute('data-url');
    const href = cur.href || (cur.getAttribute && cur.getAttribute('href'));
    const onclick = cur.getAttribute && cur.getAttribute('onclick');
    if (!action && (dataUrl || href || onclick || cur.tagName === 'BUTTON')) action = cur;
  }
  if (action) {
    return {
      dataVersionId: '',
      dataExtraUrl: '',
      dataUrl: action.getAttribute('data-url') || '',
      href: action.href || action.getAttribute('href') || '',
      onclick: action.getAttribute('onclick') || '',
      tag: action.tagName || '',
      text: normalized(action).slice(0, 300)
    };
  }
}
return null;
"""
    return driver.execute_script(script, list(aliases))


def _find_download_target(driver):
    script = r"""
const selectors = [
  '.post-download[data-url]',
  '#detail-download-button',
  'a[href*="dw.uptodown.com/dwn/"]',
  '[data-url*="/dwn/"]',
  'button[data-url]',
  'a[data-url]',
  '[data-url*="download"]',
  'a[href*="/download"]'
];
for (const selector of selectors) {
  for (const el of document.querySelectorAll(selector)) {
    const dataUrl = el.getAttribute('data-url') || '';
    const href = el.href || el.getAttribute('href') || '';
    const onclick = el.getAttribute('onclick') || '';
    const text = (el.innerText || el.textContent || '').trim().toLowerCase();
    if (dataUrl || href || onclick) return {dataUrl, href, onclick, text};
  }
}
return null;
"""
    return driver.execute_script(script)


def _click_download_target(driver) -> bool:
    """Activate a normal public download control when it hides the final URL."""
    script = r"""
const selectors = [
  '#detail-download-button',
  '.post-download',
  'button[data-url]',
  'a[data-url]',
  'a[href*="/download"]'
];
for (const selector of selectors) {
  for (const el of document.querySelectorAll(selector)) {
    if (el.disabled) continue;
    el.click();
    return true;
  }
}
return false;
"""
    return bool(driver.execute_script(script))


def _new_driver():
    try:
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options
        from selenium.webdriver.chrome.service import Service
    except ImportError as error:
        raise BrowserFallbackError("selenium Python package is not installed") from error

    chrome, driver = _driver_paths()
    if not chrome or not driver:
        raise BrowserFallbackError(
            f"runner Chrome/ChromeDriver not found (chrome={bool(chrome)}, driver={bool(driver)})"
        )

    options = Options()
    options.binary_location = chrome
    for argument in (
        "--headless=new",
        "--no-sandbox",
        "--disable-dev-shm-usage",
        "--disable-gpu",
        "--window-size=1365,1600",
        "--lang=en-US",
    ):
        options.add_argument(argument)
    options.add_experimental_option(
        "prefs",
        {
            "profile.default_content_setting_values.notifications": 2,
            "profile.managed_default_content_settings.images": 2,
        },
    )
    service = Service(executable_path=driver)
    browser = webdriver.Chrome(service=service, options=options)
    browser.set_page_load_timeout(30)
    browser.set_script_timeout(15)
    return browser


def _uptodown_names(app_name: str, package: str) -> list[str]:
    config = providers.load_config(app_name, "uptodown") or {
        "package": package,
        "name": app_name.replace("_", "-").replace(" ", "-"),
    }
    module = providers.MODULES.get("uptodown")
    generator = getattr(module, "generate_possible_uptodown_names", None)
    names = generator(config) if generator else [str(config.get("name", ""))]
    return [str(name).strip() for name in names if str(name).strip()]


def resolve_uptodown_download(
    app_name: str,
    package: str,
    candidate: VersionCandidate,
) -> BrowserDownload:
    """Render Uptodown version history and resolve the requested release."""
    aliases = tuple(dict.fromkeys(candidate.aliases("uptodown")))
    names = _uptodown_names(app_name, package)
    if not names:
        raise BrowserFallbackError("no Uptodown slug candidates configured")

    browser = _new_driver()
    errors: list[str] = []
    try:
        for slug in names:
            versions_url = f"https://{slug}.en.uptodown.com/android/versions"
            try:
                logging.info(
                    "🌐 browser fallback: rendering Uptodown history for %s via %s",
                    app_name,
                    utils.safe_url_for_log(versions_url),
                )
                browser.get(versions_url)
                _wait_for_normal_page(browser)
                if not _is_safe_uptodown_page_url(browser.current_url):
                    raise BrowserFallbackError("browser returned a non-Uptodown URL")

                target = _find_version_target(browser, aliases)
                if not target:
                    errors.append(f"{slug}: requested version not present in rendered DOM")
                    continue

                direct = _direct_url_from_target(target, browser.current_url)
                if direct:
                    return BrowserDownload(
                        direct,
                        _cookies_as_header(browser),
                        "browser-uptodown",
                    )

                page_url = _version_target_page_url(target, browser.current_url)
                if page_url:
                    browser.get(page_url)
                else:
                    browser.execute_script(
                        r"""
const aliases = arguments[0].map(v => String(v).trim().toLowerCase());
for (const el of document.querySelectorAll('a,button,div,span,li,article')) {
  const text = (el.innerText || el.textContent || '').trim().toLowerCase();
  if (!aliases.some(v => text === v || text.startsWith(v + ' '))) continue;
  let cur = el;
  for (let i = 0; cur && i < 7; i++, cur = cur.parentElement) {
    if (cur.tagName === 'A' || cur.tagName === 'BUTTON' || cur.onclick) { cur.click(); return true; }
  }
}
return false;
""",
                        list(aliases),
                    )
                _wait_for_normal_page(browser)
                time.sleep(1.0)
                if not _is_safe_uptodown_page_url(browser.current_url):
                    errors.append(f"{slug}: release navigation left Uptodown")
                    continue

                for attempt in range(2):
                    download_target = _find_download_target(browser)
                    if not download_target:
                        break
                    direct = _direct_url_from_target(download_target, browser.current_url)
                    if direct:
                        logging.info(
                            "✓ browser fallback resolved %s %s from rendered Uptodown DOM",
                            app_name,
                            candidate.describe(),
                        )
                        return BrowserDownload(
                            direct,
                            _cookies_as_header(browser),
                            "browser-uptodown",
                        )

                    # A generic /android/download href is an HTML page, not an
                    # APK. Click it once and inspect the rendered follow-up page
                    # instead of handing that HTML URL to the binary downloader.
                    if attempt == 0 and _click_download_target(browser):
                        time.sleep(1.0)
                        _wait_for_normal_page(browser)
                        if not _is_safe_uptodown_page_url(browser.current_url):
                            break
                        continue
                    break

                errors.append(
                    f"{slug}: exact release rendered but no concrete Uptodown CDN URL was exposed"
                )
            except Exception as error:
                errors.append(
                    f"{slug}: {type(error).__name__}: {utils.safe_text_for_log(error)}"
                )
                continue
    finally:
        browser.quit()

    raise BrowserFallbackError(
        "rendered Uptodown lookup failed: " + "; ".join(errors[-8:])
    )


def download_candidate(
    app_name: str,
    package: str,
    candidate: VersionCandidate,
    output_dir: Path | None = None,
) -> Path:
    """Discover with Chrome, download through the normal hardened HTTP path."""
    spec = resolve_uptodown_download(app_name, package, candidate)
    from src import downloader

    path = downloader.download_resource(
        spec.url,
        headers=spec.headers,
        validate_apk=True,
    )
    if output_dir and path.parent.resolve() != output_dir.resolve():
        output_dir.mkdir(parents=True, exist_ok=True)
        target = output_dir / path.name
        if target.resolve() != path.resolve():
            shutil.move(str(path), str(target))
            path = target
    return path
