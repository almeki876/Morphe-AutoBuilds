"""Run upstream gplaydl with optional Play-market metadata overrides.

This module intentionally does not replace or reimplement gplaydl's details,
purchase, delivery, protobuf, profile-selection, or download logic. It only
corrects market metadata supplied by the hosted gplaydl dispenser when the
caller explicitly configures a market through environment variables.

The hosted dispenser currently returns a fixed ``deviceInfoProvider.mccMnc``
and defaults its auth locale independently of the linked account. That can make
a legitimate account look like it is operating in another Play market. The
runner keeps upstream gplaydl intact while allowing the final FDFE market
headers and dispenser locale to match the account actually used by CI.
"""

from __future__ import annotations

import os
import re
import sys

_LOCALE_RE = re.compile(r"^[A-Za-z]{2,3}(?:[-_][A-Za-z]{2})?$")


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


def _install_market_overrides() -> None:
    locale = _configured_locale()
    mccmnc = _configured_mccmnc()
    if not locale and not mccmnc:
        return

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

    def build_headers(auth_data: dict) -> dict[str, str]:
        nonlocal observed
        headers = original_build_headers(auth_data)
        device_info = auth_data.get("deviceInfoProvider", {})
        upstream_mccmnc = str(device_info.get("mccMnc", "") or "")

        if locale:
            headers["Accept-Language"] = locale.replace("_", "-")
            headers["X-DFE-UserLanguages"] = locale.replace("-", "_")
        if mccmnc:
            headers["X-DFE-MCCMNC"] = mccmnc

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
