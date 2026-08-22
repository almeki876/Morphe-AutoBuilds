"""Last-resort browser discovery for public APK mirror pages.

The browser is only used to render normal public pages and discover a concrete
release URL. CAPTCHA solving is intentionally unsupported, and every downloaded
archive is still validated against the requested Android manifest identity by
the caller.
"""

from __future__ import annotations

import logging
import os
import re
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


def _is_uptodown_host(host: str) -> bool:
    value = host.casefold().rstrip(".")
    return value == "uptodown.com" or value.endswith(".uptodown.com")


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


def _is_direct_uptodown_file_url(url: str) -> bool:
    """Allow only Uptodown's concrete binary CDN URL, never arbitrary hosts."""
    parsed = urlparse(url)
    return (
        parsed.scheme == "https"
        and (parsed.hostname or "").casefold() == "dw.uptodown.com"
        and parsed.path.startswith("/dwn/")
        and len(parsed.path) > len("/dwn/")
    )


def _is_generic_uptodown_url(url: str) -> bool:
    """Return True for app-level URLs that do not identify one release.

    ``/android/download`` is the current release page and must never be treated
    as proof for a historical version. A historical release URL has an
    identifier after ``download``; direct ``dw.uptodown.com/dwn`` links are
    concrete release URLs.
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


def _version_target_page_url(target: dict, history_url: str) -> str | None:
    """Resolve one exact Uptodown version-card URL without falling to latest.

    Current Uptodown history cards can carry ``data-version-id``, ``data-url``
    and ``data-extra-url`` instead of a concrete href. For an exact historical
    card, construct ``.../android/download/<version-id>`` only when the card's
    root is itself an Uptodown URL. Generic ``/android/download`` is never
    returned.
    """
    history = urlparse(history_url)
    if history.scheme != "https" or not _is_uptodown_host(history.hostname or ""):
        return None

    data_url = str(target.get("dataUrl") or target.get("raw") or "").strip()
    href = str(target.get("href") or "").strip()
    version_id = str(target.get("dataVersionId") or "").strip()
    extra = str(target.get("dataExtraUrl") or "").strip().strip("/")

    for raw in (data_url, href):
        resolved = _safe_download_url(raw, history_url)
        if not resolved:
            continue
        parsed = urlparse(resolved)
        if parsed.scheme != "https" or not _is_uptodown_host(parsed.hostname or ""):
            continue
        if not _is_generic_uptodown_url(resolved):
            return resolved

    if not version_id or not re.fullmatch(r"[A-Za-z0-9._-]+", version_id):
        return None
    if extra and extra.casefold() != "download":
        return None

    root = _safe_download_url(data_url, history_url) if data_url else history_url
    if not root:
        return None
    parsed_root = urlparse(root)
    if parsed_root.scheme != "https" or not _is_uptodown_host(parsed_root.hostname or ""):
        return None

    path = parsed_root.path.rstrip("/")
    if path.endswith("/versions"):
        path = path[: -len("/versions")]
    if path.endswith("/download"):
        path = path[: -len("/download")]
    if not path.endswith("/android"):
        return None
    return f"{parsed_root.scheme}://{parsed_root.netloc}{path}/download/{version_id}"


def _direct_url_from_target(target: dict, page_url: str) -> str | None:
    """Return only a concrete Uptodown CDN file URL from a rendered target."""
    values = [
        str(target.get("dataUrl") or ""),
        str(target.get("href") or ""),
    ]
    onclick = str(target.get("onclick") or "")
    if onclick:
        match = re.search(r"(?:https?://[^'\"\s)]+|/dwn/[^'\"\s)]+)", onclick)
        if match:
            values.append(match.group(0))

    for raw in values:
        direct = _safe_download_url(raw, page_url)
        if direct and _is_direct_uptodown_file_url(direct):
            return direct
    return None


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
    """Find an exact short version label and return its nearest release card."""
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
    if (card.getAttribute) {
      const versionId = card.getAttribute('data-version-id') || '';
      const dataUrl = card.getAttribute('data-url') || card.getAttribute('data-href') || '';
      const extraUrl = card.getAttribute('data-extra-url') || '';
      const href = card.getAttribute('href') || card.href || '';
      const onclick = card.getAttribute('onclick') || '';
      if (versionId) {
        return {dataVersionId: String(versionId), dataExtraUrl: String(extraUrl), dataUrl: String(dataUrl), href: String(href), onclick: String(onclick), raw: String(dataUrl || href), text};
      }
    }
    const candidates = [card, ...Array.from(card.querySelectorAll ? card.querySelectorAll('a[href],[data-url],[data-href],button[data-url]') : [])];
    for (const candidate of candidates) {
      if (!candidate.getAttribute) continue;
      const dataUrl = candidate.getAttribute('data-url') || candidate.getAttribute('data-href') || '';
      const href = candidate.getAttribute('href') || candidate.href || '';
      const onclick = candidate.getAttribute('onclick') || '';
      const raw = dataUrl || href;
      if (!raw) continue;
      const lower = String(raw).toLowerCase();
      if (/\/download\/[^/?#]+/.test(lower) || lower.includes('dw.uptodown.com/dwn/') || lower.startsWith('/dwn/')) {
        return {dataVersionId: '', dataExtraUrl: '', dataUrl: String(dataUrl), href: String(href), onclick: String(onclick), raw: String(raw), text};
      }
    }
  }
  return {dataVersionId: '', dataExtraUrl: '', dataUrl: '', href: '', onclick: '', raw: '', text};
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
  'button[data-url]',
  'a[data-url]'
];
for (const selector of selectors) {
  for (const el of document.querySelectorAll(selector)) {
    const dataUrl = el.getAttribute('data-url') || el.getAttribute('data-href') || '';
    const href = el.href || el.getAttribute('href') || '';
    const onclick = el.getAttribute('onclick') || '';
    if (dataUrl || href || onclick) return {dataUrl, href, onclick};
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
                    if not _is_uptodown_host(urlparse(browser.current_url).hostname or ""):
                        raise BrowserFallbackError("browser left the Uptodown domain")

                    target = _expand_version_history(browser, aliases)
                    if not target:
                        errors.append(
                            f"{slug}.{locale}: requested version not present after bounded history expansion"
                        )
                        continue

                    direct = _direct_url_from_target(target, browser.current_url)
                    if direct:
                        return BrowserDownload(
                            direct, _cookies_as_header(browser), "browser-uptodown"
                        )

                    page_url = _version_target_page_url(target, browser.current_url)
                    if page_url:
                        browser.get(page_url)
                    else:
                        if not _click_exact_version_target(browser, aliases):
                            errors.append(
                                f"{slug}.{locale}: exact version card had no release-specific link"
                            )
                            continue

                    _wait_for_normal_page(browser)
                    time.sleep(0.5)
                    if _is_generic_uptodown_url(browser.current_url):
                        errors.append(
                            f"{slug}.{locale}: exact history navigation fell back to current download page"
                        )
                        continue

                    target = _find_download_target(browser)
                    if not target:
                        errors.append(
                            f"{slug}.{locale}: rendered release page had no direct CDN download target"
                        )
                        continue
                    direct = _direct_url_from_target(target, browser.current_url)
                    if not direct:
                        errors.append(
                            f"{slug}.{locale}: release page did not expose an Uptodown CDN file URL"
                        )
                        continue
                    logging.info(
                        "✓ browser fallback resolved %s %s from exact Uptodown history card",
                        app_name,
                        candidate.describe(),
                    )
                    return BrowserDownload(
                        direct, _cookies_as_header(browser), "browser-uptodown"
                    )
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
    """Convert XAPK/APKM/APKS-style split bundles to one APK with APKEditor."""
    from src import apk_validation, downloader

    try:
        apk_validation.assert_valid_apk_archive(path)
        return path
    except Exception as original_error:
        if not zipfile.is_zipfile(path):
            raise BrowserFallbackError(
                "download was neither an APK nor an APK bundle"
            ) from original_error
        with zipfile.ZipFile(path) as archive:
            apk_members = [
                name for name in archive.namelist() if name.casefold().endswith(".apk")
            ]
        if not apk_members:
            raise BrowserFallbackError(
                "downloaded ZIP contained no APK modules"
            ) from original_error

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
    logging.info(
        "✓ merged split APK bundle into standalone APK before identity validation"
    )
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
