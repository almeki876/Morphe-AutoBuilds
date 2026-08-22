"""Runtime compatibility shim for upstream gplaydl.

Upstream gplaydl 4.2.1 hard-codes English Play FDFE locale headers even when
its linked Google account and device metadata are for another country. Keep the
upstream package intact and override only those request headers at interpreter
startup. Also expose the purchase response outcome without logging credentials
or opaque tokens, so Play acquisition failures can be diagnosed precisely.
"""

from __future__ import annotations

import os
import sys


def _install() -> None:
    locale = os.getenv("GPLAYDL_PLAY_LOCALE", "ja-JP").strip()
    if not locale:
        return

    try:
        import httpx
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

    def purchase(package: str, version_code: int, auth_data: dict) -> str:
        """Mirror upstream purchase() while logging only non-secret outcome fields."""
        headers = build_headers(auth_data)
        headers["Content-Type"] = "application/x-www-form-urlencoded"
        body = f"doc={package}&ot=1&vc={version_code}"
        resp = httpx.post(api.PURCHASE_URL, headers=headers, content=body, timeout=30)
        if resp.status_code == 401:
            raise api.AuthExpiredError("Auth token expired.")
        if resp.status_code not in (200, 204):
            print(
                f"gplaydl purchase diagnostics: http={resp.status_code} token=false",
                file=sys.stderr,
            )
            return ""

        buy_fields = api._navigate(resp.content, 1, 4)
        token = api._first_string(buy_fields, 55) if buy_fields else ""
        purchase_fields = []
        if buy_fields:
            purchase_payload = api._first_bytes(buy_fields, 1)
            if purchase_payload:
                purchase_fields = api.ProtoDecoder(purchase_payload).read_all_ordered()
        status = api._first_int(purchase_fields, 1) if purchase_fields else None
        localized_error = (
            api._first_string(purchase_fields, 3) if purchase_fields else ""
        )
        safe_error = " ".join(localized_error.split())[:180]
        print(
            "gplaydl purchase diagnostics: "
            f"http={resp.status_code} status={status if status is not None else 'unknown'} "
            f"token={'true' if token else 'false'}"
            + (f" message={safe_error}" if safe_error else ""),
            file=sys.stderr,
        )
        return token

    # api.py imports build_headers directly, so patch both module bindings.
    auth.build_headers = build_headers
    api.build_headers = build_headers
    api.purchase = purchase

    # cli.py imported purchase directly at module load, so patch that binding too.
    try:
        from gplaydl import cli
        cli.purchase = purchase
    except Exception:
        pass


_install()
