"""Diagnostic shim for upstream gplaydl 4.2.1.

Do not alter Google Play request semantics here.  The repository loads this
module only to expose enough non-secret information to diagnose acquisition
failures while preserving upstream gplaydl's headers, body format, auth and
endpoint sequence exactly.
"""

from __future__ import annotations

import sys


def _safe_response_preview(resp) -> str:
    """Return a bounded, printable preview of a non-success Play response."""
    content_type = (resp.headers.get("content-type") or "").lower()
    raw = resp.content or b""
    if not raw:
        return "empty"
    if "text/" in content_type or "json" in content_type:
        text = " ".join(resp.text.split())[:240]
        return text or "empty-text"
    return "hex:" + raw[:48].hex()


def _install() -> None:
    try:
        import httpx
        from gplaydl import api
    except Exception:
        return

    def purchase(package: str, version_code: int, auth_data: dict) -> str:
        """Mirror upstream purchase() exactly, adding only safe diagnostics."""
        headers = api.build_headers(auth_data)
        headers["Content-Type"] = "application/x-www-form-urlencoded"
        body = f"doc={package}&ot=1&vc={version_code}"
        resp = httpx.post(api.PURCHASE_URL, headers=headers, content=body, timeout=30)
        if resp.status_code == 401:
            raise api.AuthExpiredError("Auth token expired.")
        if resp.status_code not in (200, 204):
            print(
                "gplaydl purchase diagnostics: "
                f"http={resp.status_code} vc={version_code} wire=upstream-form "
                f"content_type={resp.headers.get('content-type', '')!r} "
                f"response={_safe_response_preview(resp)!r}",
                file=sys.stderr,
            )
            return ""

        buy_fields = api._navigate(resp.content, 1, 4)
        token = api._first_string(buy_fields, 55) if buy_fields else ""
        print(
            "gplaydl purchase diagnostics: "
            f"http={resp.status_code} vc={version_code} wire=upstream-form "
            f"token={'true' if token else 'false'}",
            file=sys.stderr,
        )
        return token

    api.purchase = purchase
    try:
        from gplaydl import cli
        cli.purchase = purchase
    except Exception:
        pass


_install()
