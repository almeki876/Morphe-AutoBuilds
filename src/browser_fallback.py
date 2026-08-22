"""Last-resort browser discovery for mirror pages that require JavaScript.

This module deliberately does not solve CAPTCHAs or weaken APK identity checks.
It only renders normal public pages with the Chrome/ChromeDriver already present
on GitHub's Ubuntu runner, discovers a concrete download URL, and returns it to
the normal downloader. The caller must still validate the downloaded manifest.
"""

from __future__ import annotations

import logging
import os
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
    # Uptodown's detail-download-button exposes only the signed token in
    # data-url. Do not log this value; safe_url_for_log redacts it later.
    if "/" not in value and len(value) > 20:
        return f"https://dw.uptodown.com/dwn/{value}"
    return urljoin(page_url, value)


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
const nodes = Array.from(document.querySelectorAll('a,button,[data-url],div,span,li,article'));
function normalized(el) { return (el.innerText || el.textContent || '').trim().toLowerCase(); }
for (const el of nodes) {
  const text = normalized(el);
  if (!text || !aliases.some(v => text === v || text.startsWith(v + ' ') || text.includes(' ' + v + ' '))) continue;
  let cur = el;
  for (let depth = 0; cur && depth < 7; depth++, cur = cur.parentElement) {
    const dataUrl = cur.getAttribute && cur.getAttribute('data-url');
    const href = cur.href || (cur.getAttribute && cur.getAttribute('href'));
    if (dataUrl || href || cur.tagName === 'BUTTON') {
      return {dataUrl: dataUrl || '', href: href || '', tag: cur.tagName || '', text: normalized(cur).slice(0, 300)};
    }
  }
}
return null;
"""
    return driver.execute_script(script, list(aliases))


def _find_download_target(driver):
    script = r"""
const selectors = [
  '#detail-download-button',
  '[data-url*="download"]',
  'a[href*="/download"]',
  'a[href*="dw.uptodown.com"]',
  'button[data-url]',
  'a[data-url]'
];
for (const selector of selectors) {
  for (const el of document.querySelectorAll(selector)) {
    const dataUrl = el.getAttribute('data-url') || '';
    const href = el.href || el.getAttribute('href') || '';
    const text = (el.innerText || el.textContent || '').trim().toLowerCase();
    if (dataUrl || href) return {dataUrl, href, text};
  }
}
return null;
"""
    return driver.execute_script(script)


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
                if urlparse(browser.current_url).hostname is None:
                    raise BrowserFallbackError("browser returned an invalid URL")

                target = _find_version_target(browser, aliases)
                if not target:
                    errors.append(f"{slug}: requested version not present in rendered DOM")
                    continue

                raw = target.get("dataUrl") or target.get("href") or ""
                page_url = _safe_download_url(raw, browser.current_url)
                if page_url and "dw.uptodown.com/dwn/" in page_url:
                    return BrowserDownload(
                        page_url,
                        _cookies_as_header(browser),
                        "browser-uptodown",
                    )

                if page_url:
                    browser.get(page_url)
                else:
                    # A button with no URL: click the exact element by version text.
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

                target = _find_download_target(browser)
                if not target:
                    errors.append(f"{slug}: rendered release page had no download target")
                    continue
                direct = _safe_download_url(
                    target.get("dataUrl") or target.get("href") or "",
                    browser.current_url,
                )
                if not direct:
                    errors.append(f"{slug}: download target had no usable URL")
                    continue
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
