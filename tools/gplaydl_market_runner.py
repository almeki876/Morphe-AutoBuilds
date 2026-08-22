"""Run upstream gplaydl with optional Play-market metadata overrides.

This module intentionally does not replace or reimplement gplaydl's details,
purchase, delivery, profile selection, or download logic. It only corrects
market metadata supplied by the hosted gplaydl dispenser when the caller
explicitly configures a market through environment variables.

The current hosted dispenser mints a Play auth bundle with fixed regional
metadata that is independent of the linked account. Besides overriding the
final FDFE locale/MCC-MNC headers, this runner refreshes the TOC cookie through
the same current FDFE ``/toc`` endpoint used by gplaydl-dispenser, so the
cookie and the subsequent upstream gplaydl requests use one market context.
"""

from __future__ import annotations

import os
import re
import sys

_LOCALE_RE = re.compile(r"^[A-Za-z]{2,3}(?:[-_][A-Za-z]{2})?$")
_TOC_URL = "https://android.clients.google.com/fdfe/toc"


def _configured_locale() -> str:
    value = os.getenv("GPLAYDL_MARKET_LOCALE", "").strip()
    if value and not _LOCALE_RE.fullmatch(value):
        raise ValueError(f"invalid GPLAYDL_MARKET_LOCALE: {value!r}")
    return value


def _configured_mccmnc() -> str:
    value = os.getenv("GPLAYDL_MARKET_MCCMNC", "").strip()
    if value and (not value.isdigit() or len(value) not in (5, 6)):
        raise ValueError("GPLAYDL_MARKET_MCCMNC must be a 5- or 6-digit MCC/MNC")
    return value


def _first_bytes(fields, number: int) -> bytes | None:
    for field_number, wire_type, value in fields:
        if field_number == number and wire_type == 2 and isinstance(value, (bytes, bytearray)):
            return bytes(value)
    return None


def _toc_cookie(raw: bytes) -> str:
    """Decode ResponseWrapper(1) -> Payload(6) -> TocResponse.cookie(22)."""
    from gplaydl.protobuf import ProtoDecoder

    wrapper = ProtoDecoder(raw).read_all_ordered()
    payload_raw = _first_bytes(wrapper, 1)
    if not payload_raw:
        return ""
    payload = ProtoDecoder(payload_raw).read_all_ordered()
    toc_raw = _first_bytes(payload, 6)
    if not toc_raw:
        return ""
    toc = ProtoDecoder(toc_raw).read_all_ordered()
    cookie_raw = _first_bytes(toc, 22)
    return ProtoDecoder.decode_string(cookie_raw) if cookie_raw else ""


def _install_market_overrides() -> None:
    locale = _configured_locale()
    mccmnc = _configured_mccmnc()
    if not locale and not mccmnc:
        return

    import httpx
    from gplaydl import auth

    if locale:
        original_dispenser_request = auth._dispenser_request

        def dispenser_request(dispenser_url, email):
            url, headers, params = original_dispenser_request(dispenser_url, email)
            query = dict(params or {})
            query["locale"] = locale
            return url, headers, query

        auth._dispenser_request = dispenser_request

    original_build_headers = auth.build_headers
    observed = False

    def market_headers(auth_data: dict) -> dict[str, str]:
        headers = original_build_headers(auth_data)
        if locale:
            headers["Accept-Language"] = locale.replace("_", "-")
            headers["X-DFE-UserLanguages"] = locale.replace("-", "_")
        if mccmnc:
            headers["X-DFE-MCCMNC"] = mccmnc
        return headers

    def refresh_toc_cookie(auth_data: dict) -> dict:
        """Refresh only the market-sensitive TOC cookie using current auth data."""
        if not auth_data or not auth_data.get("authToken"):
            return auth_data

        headers = market_headers(auth_data)
        # gplaydl-dispenser obtains its initial TOC cookie without a prior cookie.
        headers["X-DFE-Cookie"] = ""
        try:
            response = httpx.get(_TOC_URL, headers=headers, timeout=30)
        except Exception as exc:
            print(
                f"gplaydl market TOC refresh: error={type(exc).__name__} cookie=false",
                file=sys.stderr,
            )
            return auth_data

        cookie = _toc_cookie(response.content) if response.status_code == 200 else ""
        print(
            "gplaydl market TOC refresh: "
            f"http={response.status_code} cookie={'true' if cookie else 'false'}",
            file=sys.stderr,
        )
        if cookie:
            auth_data = dict(auth_data)
            auth_data["dfeCookie"] = cookie
        return auth_data

    # Every token path (default profile and compatibility-profile retries) passes
    # through _request_token, so refresh the TOC cookie once immediately after
    # the current hosted dispenser returns the auth bundle. No credentials are
    # logged or persisted beyond gplaydl's normal auth cache.
    original_request_token = auth._request_token

    def request_token(url, headers, params, profile):
        data = original_request_token(url, headers, params, profile)
        return refresh_toc_cookie(data) if data else data

    auth._request_token = request_token

    def build_headers(auth_data: dict) -> dict[str, str]:
        nonlocal observed
        headers = market_headers(auth_data)
        device_info = auth_data.get("deviceInfoProvider", {})
        upstream_mccmnc = str(device_info.get("mccMnc", "") or "")

        if not observed:
            print(
                "gplaydl market metadata: "
                f"upstream_mccmnc={upstream_mccmnc or 'unset'} "
                f"effective_mccmnc={headers.get('X-DFE-MCCMNC', 'unset')} "
                f"locale={headers.get('X-DFE-UserLanguages', 'unset')} "
                "purchase=upstream",
                file=sys.stderr,
            )
            observed = True
        return headers

    auth.build_headers = build_headers

    # gplaydl.api imports build_headers into its own module namespace. Replace
    # only that binding; all API functions themselves remain upstream code.
    from gplaydl import api

    api.build_headers = build_headers


def main() -> None:
    _install_market_overrides()
    from gplaydl.cli import app

    app()


if __name__ == "__main__":
    main()
