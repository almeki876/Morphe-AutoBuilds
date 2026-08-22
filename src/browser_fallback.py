"""Last-resort browser discovery for public APK mirror pages.

The browser is only used to render normal public pages and discover a concrete
release URL. CAPTCHA solving is intentionally unsupported, and every downloaded
archive is still validated against the requested Android manifest identity by
the caller.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import time
import zipfile
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
_UPTODOWN_LOCALES = ("en", "id", "jp", "cn")


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


def _is_generic_uptodown_url(url: str) -> bool:
    """Return True for app-level URLs that do not identify one release.

    In particular, ``/android/download`` is the *current* release page and must
    never be treated as proof for a historical version. A historical release
    URL has an identifier after ``download``; direct ``dw.uptodown.com/dwn``
    links are also concrete release URLs.
    """
    parsed = urlparse(url)
    host = (parsed.hostname or "").casefold()
    path = parsed.path.rstrip("/")
    if host == "dw.uptodown.com" and path.startswith("/dwn/"):
        return False
    parts = [part for part in path.casefold().split("/") if part]
    if "download" not in parts:
        return True
    index = max(i for i, part in enumerate(parts) if part == "download")
    return index == len(parts) - 1


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
    """Allow ordinary JS rendering, but never solve a CAPTCHA."""
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
    """Find an exact, short version label and a link in its nearest card.

    This deliberately ignores large containers whose text merely *contains* a
    requested version. That prevents an old version label somewhere in history
    from accidentally authorizing the app-level current ``/android/download``
    link.
    """
    script = r"""
const aliases = arguments[0].map(v => String(v).trim().toLowerCase()).filter(Boolean);
function norm(text) { return String(text || '').replace(/\s+/g, ' ').trim().toLowerCase(); }
function matches(text) {
  if (!text || text.length > 120) return false;
  return aliases.some(v => text === v || text.startsWith(v + ' ') || text.endsWith(' ' + v));
}
const nodes = Array.from(document.querySelectorAll('.version,[class*="version"],span,div,p,strong,b'));
for (const node of nodes) {
  const text = norm(node.innerText || node.textContent);
  if (!matches(text)) continue;
  let card = node;
  for (let depth = 0; card && depth < 7; depth++, card = card.parentElement) {
    const candidates = [card, ...Array.from(card.querySelectorAll ? card.querySelectorAll('a[href],[data-url],[data-href],button[data-url]') : [])];
    let generic = '';
    for (const candidate of candidates) {
      const raw = (candidate.getAttribute && (candidate.getAttribute('data-url') || candidate.getAttribute('data-href') || candidate.getAttribute('href'))) || candidate.href || '';
      if (!raw) continue;
      const lower = String(raw).toLowerCase();
      if (/\/download\/[^/?#]+/.test(lower) || lower.includes('dw.uptodown.com/dwn/') || lower.startsWith('/dwn/')) {
        return {raw: String(raw), text, concrete: true};
      }
      if (!generic && lower.includes('/download')) generic = String(raw);
    }
    if (generic) return {raw: generic, text, concrete: false};
  }
  return {raw: '', text, concrete: false};
}
return null;
"""
    return driver.execute_script(script, list(aliases))


def _click_exact_version_target(driver, aliases: tuple[str, ...]) -> bool:
    script = r"""
const aliases = arguments[0].map(v => String(v).trim().toLowerCase()).filter(Boolean);
function norm(text) { return String(text || '').replace(/\s+/g, ' ').trim().toLowerCase(); }
function matches(text) {
  if (!text || text.length > 120) return false;
  return aliases.some(v => text === v || text.startsWith(v + ' ') || text.endsWith(' ' + v));
}
for (const node of document.querySelectorAll('.version,[class*="version"],span,div,p,strong,b')) {
  if (!matches(norm(node.innerText || node.textContent))) continue;
  let card = node;
  for (let depth = 0; card && depth < 7; depth++, card = card.parentElement) {
    const clickable = card.matches && card.matches('a,button,[role="button"]') ? card :
      (card.querySelector && card.querySelector('a[href],button,[role="button"]'));
    if (clickable) { clickable.click(); return true; }
  }
}
return false;
"""
    return bool(driver.execute_script(script, list(aliases)))


def _expand_version_history(driver, aliases: tuple[str, ...], rounds: int = 10):
    """Boundedly expand lazy-loaded history until the exact version appears."""
    previous_height = -1
    stagnant = 0
    for _ in range(rounds):
        target = _find_version_target(driver, aliases)
        if target:
            return target
        state = driver.execute_script(
            r"""
const before = document.body ? document.body.scrollHeight : 0;
window.scrollTo(0, before);
const labels = ['load more','show more','see more','more versions','older versions','more','もっと見る'];
let clicked = false;
for (const el of document.querySelectorAll('button,a,[role="button"]')) {
  const text = String(el.innerText || el.textContent || '').replace(/\s+/g,' ').trim().toLowerCase();
  if (!text || !labels.some(v => text === v || text.startsWith(v + ' '))) continue;
  const rect = el.getBoundingClientRect();
  if (rect.width <= 0 || rect.height <= 0) continue;
  el.click(); clicked = true; break;
}
return {height: before, clicked};
"""
        ) or {}
        time.sleep(0.8)
        _wait_for_normal_page(driver, timeout=4.0)
        height = int(state.get("height") or 0)
        if not state.get("clicked") and height == previous_height:
            stagnant += 1
            if stagnant >= 2:
                break
        else:
            stagnant = 0
        previous_height = height
    return _find_version_target(driver, aliases)


def _find_download_target(driver):
    script = r"""
const selectors = [
  '#detail-download-button',
  '[data-url*="/dwn/"]',
  'a[href*="dw.uptodown.com/dwn/"]',
  '[data-url*="download"]',
  'a[href*="/download/"]',
  'button[data-url]',
  'a[data-url]'
];
for (const selector of selectors) {
  for (const el of document.querySelectorAll(selector)) {
    const dataUrl = el.getAttribute('data-url') || el.getAttribute('data-href') || '';
    const href = el.href || el.getAttribute('href') || '';
    if (dataUrl || href) return {dataUrl, href};
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
    return list(dict.fromkeys(str(name).strip() for name in names if str(name).strip()))


def resolve_uptodown_download(
    app_name: str,
    package: str,
    candidate: VersionCandidate,
) -> BrowserDownload:
    """Render Uptodown history and resolve only the requested release."""
    aliases = tuple(dict.fromkeys(candidate.aliases("uptodown")))
    names = _uptodown_names(app_name, package)
    if not names:
        raise BrowserFallbackError("no Uptodown slug candidates configured")

    browser = _new_driver()
    errors: list[str] = []
    try:
        for slug in names:
            for locale in _UPTODOWN_LOCALES:
                versions_url = f"https://{slug}.{locale}.uptodown.com/android/versions"
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

                    target = _expand_version_history(browser, aliases)
                    if not target:
                        errors.append(f"{slug}.{locale}: requested version not present after bounded history expansion")
                        continue

                    raw = target.get("raw") or ""
                    page_url = _safe_download_url(raw, browser.current_url)
                    if page_url and not _is_generic_uptodown_url(page_url):
                        if "dw.uptodown.com/dwn/" in page_url:
                            return BrowserDownload(page_url, _cookies_as_header(browser), "browser-uptodown")
                        browser.get(page_url)
                    else:
                        if not _click_exact_version_target(browser, aliases):
                            errors.append(f"{slug}.{locale}: exact version card had no release-specific link")
                            continue

                    _wait_for_normal_page(browser)
                    time.sleep(0.5)
                    target = _find_download_target(browser)
                    if not target:
                        errors.append(f"{slug}.{locale}: rendered release page had no download target")
                        continue
                    direct = _safe_download_url(
                        target.get("dataUrl") or target.get("href") or "",
                        browser.current_url,
                    )
                    if not direct or _is_generic_uptodown_url(direct):
                        errors.append(f"{slug}.{locale}: download target was not release-specific")
                        continue
                    logging.info(
                        "✓ browser fallback resolved %s %s from exact Uptodown history card",
                        app_name,
                        candidate.describe(),
                    )
                    return BrowserDownload(direct, _cookies_as_header(browser), "browser-uptodown")
                except Exception as error:
                    errors.append(
                        f"{slug}.{locale}: {type(error).__name__}: {utils.safe_text_for_log(error)}"
                    )
    finally:
        browser.quit()

    raise BrowserFallbackError(
        "rendered Uptodown lookup failed: " + "; ".join(errors[-12:])
    )


def _normalize_apk_bundle(path: Path) -> Path:
    """Convert XAPK/APKM/APKS-style split bundles to one APK with APKEditor.

    Ordinary APKs are returned untouched. Bundles are only accepted when they
    are valid ZIPs containing nested APK modules, and the merged output must be
    a structurally valid APK. Manifest package/version validation still happens
    afterwards in ``scripts/download_apks.py``.
    """
    from src import apk_validation, downloader

    try:
        apk_validation.assert_valid_apk_archive(path)
        return path
    except Exception as original_error:
        if not zipfile.is_zipfile(path):
            raise BrowserFallbackError("download was neither an APK nor an APK bundle") from original_error
        with zipfile.ZipFile(path) as archive:
            apk_members = [name for name in archive.namelist() if name.casefold().endswith(".apk")]
        if not apk_members:
            raise BrowserFallbackError("downloaded ZIP contained no APK modules") from original_error

    editor = downloader.download_apkeditor()
    merged = path.with_name(path.stem + "-merged.apk")
    merged.unlink(missing_ok=True)
    result = subprocess.run(
        [
            "java",
            "-jar",
            str(editor),
            "m",
            "-i",
            str(path),
            "-o",
            str(merged),
            "-f",
            "-validate-modules",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=180,
        check=False,
    )
    if result.returncode != 0 or not merged.is_file():
        merged.unlink(missing_ok=True)
        raise BrowserFallbackError(
            "APKEditor could not merge the downloaded split bundle: "
            + utils.safe_text_for_log((result.stdout or "")[-1200:])
        )
    apk_validation.assert_valid_apk_archive(merged)
    path.unlink(missing_ok=True)
    logging.info("✓ merged split APK bundle into standalone APK before identity validation")
    return merged


def download_candidate(
    app_name: str,
    package: str,
    candidate: VersionCandidate,
    output_dir: Path | None = None,
) -> Path:
    """Discover with Chrome, download through hardened HTTP, normalize bundles."""
    spec = resolve_uptodown_download(app_name, package, candidate)
    from src import downloader

    path = downloader.download_resource(
        spec.url,
        headers=spec.headers,
        validate_apk=False,
    )
    path = _normalize_apk_bundle(path)
    if output_dir and path.parent.resolve() != output_dir.resolve():
        output_dir.mkdir(parents=True, exist_ok=True)
        target = output_dir / path.name
        if target.resolve() != path.resolve():
            shutil.move(str(path), str(target))
            path = target
    return path
