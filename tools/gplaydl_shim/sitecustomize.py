"""Runtime compatibility shim for upstream gplaydl.

Upstream gplaydl 4.2.1 hard-codes English Play FDFE locale headers even when
its linked Google account and device metadata are for another country. Keep the
upstream package intact and override only those request headers at interpreter
startup.

For free-app acquisition, established Google Play API implementations first
send a protobuf LogRequest containing ``confirmFreeDownload?doc=<package>`` and
then POST ``doc/ot/vc`` to ``/purchase`` as query parameters. gplaydl 4.2.1
omits that confirmation log and sends the purchase parameters in the request
body; Google Play has been observed returning HTTP 400 for that wire shape.
This shim preserves gplaydl's auth, profile, delivery and download logic while
making those two acquisition requests compatible.
"""

from __future__ import annotations

import os
import sys
import time


def _encode_varint(value: int) -> bytes:
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def _log_request(package: str) -> bytes:
    """Encode LogRequest(timestamp=1, downloadConfirmationQuery=2)."""
    timestamp = int(time.time())
    query = f"confirmFreeDownload?doc={package}".encode("utf-8")
    return b"\x08" + _encode_varint(timestamp) + b"\x12" + _encode_varint(len(query)) + query


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

    def confirm_free_download(package: str, auth_data: dict) -> int:
        headers = build_headers(auth_data)
        headers["Content-Type"] = "application/x-protobuf"
        headers["Accept"] = "application/x-protobuf"
        log_url = getattr(api, "LOG_URL", f"{api.FDFE_URL}/log")
        resp = httpx.post(
            log_url,
            headers=headers,
            content=_log_request(package),
            timeout=30,
        )
        if resp.status_code == 401:
            raise api.AuthExpiredError("Auth token expired.")
        print(
            f"gplaydl free-download confirmation: http={resp.status_code}",
            file=sys.stderr,
        )
        return resp.status_code

    def purchase(package: str, version_code: int, auth_data: dict) -> str:
        """Acquire a free app using the established FDFE request sequence."""
        confirm_free_download(package, auth_data)

        headers = build_headers(auth_data)
        headers.pop("Content-Type", None)
        params = {"doc": package, "ot": "1", "vc": str(version_code)}
        resp = httpx.post(api.PURCHASE_URL, headers=headers, params=params, timeout=30)
        if resp.status_code == 401:
            raise api.AuthExpiredError("Auth token expired.")
        if resp.status_code not in (200, 204):
            print(
                f"gplaydl purchase diagnostics: http={resp.status_code} token=false wire=query confirm=true",
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
        localized_error = api._first_string(purchase_fields, 3) if purchase_fields else ""
        safe_error = " ".join(localized_error.split())[:180]
        print(
            "gplaydl purchase diagnostics: "
            f"http={resp.status_code} status={status if status is not None else 'unknown'} "
            f"token={'true' if token else 'false'} wire=query confirm=true"
            + (f" message={safe_error}" if safe_error else ""),
            file=sys.stderr,
        )
        return token

    auth.build_headers = build_headers
    api.build_headers = build_headers
    api.purchase = purchase

    try:
        from gplaydl import cli
        cli.purchase = purchase
    except Exception:
        pass


_install()
