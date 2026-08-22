"""Runtime compatibility shim for upstream gplaydl.

Upstream gplaydl 4.2.1 hard-codes English Play FDFE locale headers even when
its linked Google account and device metadata are for another country. Keep the
upstream package intact and override only those request headers at interpreter
startup. The locale is repository/runtime policy rather than app-specific data.
"""

from __future__ import annotations

import os


def _install() -> None:
    locale = os.getenv("GPLAYDL_PLAY_LOCALE", "ja-JP").strip()
    if not locale:
        return

    try:
        from gplaydl import api, auth
    except Exception:
        return

    original = auth.build_headers
    language_tag = locale.replace("_", "-")
    user_language = locale.replace("-", "_")

    def build_headers(auth_data: dict) -> dict[str, str]:
        headers = original(auth_data)
        headers["Accept-Language"] = language_tag
        headers["X-DFE-UserLanguages"] = user_language
        return headers

    # api.py imports build_headers directly, so patch both module bindings.
    auth.build_headers = build_headers
    api.build_headers = build_headers


_install()
