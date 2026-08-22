"""Keep upstream gplaydl FDFE locale headers aligned with its AuthBundle.

Current gplaydl 4.x receives market locale metadata from the dispenser, but its
``build_headers`` helper still hardcodes ``en_US``/``en-US`` for FDFE requests.
That can make the final details/purchase/delivery requests disagree with the
market used while minting the Google auth bundle.

This module performs one narrow, fail-closed source adaptation on the installed
upstream package before the upstream ``gplaydl`` CLI is launched. It does not
change purchase/delivery semantics or add package-specific behavior.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path


_DYNAMIC_MARKER = 'locale = str(auth.get("locale") or device_info.get("localeString") or "en_US")'


def patch_auth_headers(path: Path) -> bool:
    """Make current upstream gplaydl headers follow the dispenser AuthBundle.

    Returns ``True`` when a patch was applied and ``False`` when the installed
    upstream already contains this adaptation. Any other source shape fails
    closed so an upstream change is reviewed instead of being patched blindly.
    """
    text = path.read_text(encoding="utf-8")
    if _DYNAMIC_MARKER in text and '"Accept-Language": accept_language' in text:
        return False

    locale_old = '    locale = "en_US"\n'
    locale_new = (
        '    locale = str(auth.get("locale") or device_info.get("localeString") or "en_US").strip() or "en_US"\n'
        '    accept_language = locale.replace("_", "-")\n'
    )
    language_old = '        "Accept-Language": "en-US",\n'
    language_new = '        "Accept-Language": accept_language,\n'

    if text.count(locale_old) != 1 or text.count(language_old) != 1:
        raise RuntimeError(
            "installed upstream gplaydl changed around FDFE locale headers; "
            "refusing to apply a stale market-header adaptation"
        )

    text = text.replace(locale_old, locale_new, 1)
    text = text.replace(language_old, language_new, 1)
    path.write_text(text, encoding="utf-8")
    return True


def ensure_auth_bundle_locale_headers() -> Path:
    """Patch the installed upstream gplaydl auth module and return its path."""
    spec = importlib.util.find_spec("gplaydl.auth")
    if spec is None or not spec.origin:
        raise RuntimeError("installed upstream gplaydl.auth could not be located")
    path = Path(spec.origin)
    if not path.is_file():
        raise RuntimeError(f"installed upstream gplaydl.auth is not a file: {path}")
    patch_auth_headers(path)
    return path
