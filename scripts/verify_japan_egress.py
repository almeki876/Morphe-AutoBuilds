"""Fail closed unless independent services confirm Japanese outbound traffic."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from urllib.request import Request, urlopen


EXPECTED_COUNTRY = "JP"
MIN_CONFIRMATIONS = 2
ATTEMPTS = 4
RETRY_SECONDS = 5
TIMEOUT_SECONDS = 10


def _request(url: str) -> str:
    request = Request(url, headers={"User-Agent": "Morphe-AutoBuilds/egress-check"})
    with urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        return response.read().decode("utf-8", errors="replace").strip()


def _cloudflare_country() -> str:
    payload = json.loads(_request("https://speed.cloudflare.com/meta"))
    return str(payload.get("country", "")).strip().upper()


def _country_is_country() -> str:
    payload = json.loads(_request("https://api.country.is/"))
    return str(payload.get("country", "")).strip().upper()


def _ipapi_country() -> str:
    return _request("https://ipapi.co/country/").strip().upper()


PROBES: tuple[tuple[str, Callable[[], str]], ...] = (
    ("cloudflare", _cloudflare_country),
    ("country.is", _country_is_country),
    ("ipapi", _ipapi_country),
)


def probe_countries() -> dict[str, str]:
    results: dict[str, str] = {}
    for name, probe in PROBES:
        try:
            country = probe()
        except Exception as error:
            logging.warning("Egress geolocation probe %s failed: %s", name, error)
            continue
        if len(country) == 2 and country.isalpha():
            results[name] = country
        else:
            logging.warning("Egress geolocation probe %s returned invalid data", name)
    return results


def is_verified_japan(results: dict[str, str]) -> bool:
    countries = list(results.values())
    return (
        countries.count(EXPECTED_COUNTRY) >= MIN_CONFIRMATIONS
        and all(country == EXPECTED_COUNTRY for country in countries)
    )


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    for attempt in range(1, ATTEMPTS + 1):
        results = probe_countries()
        logging.info(
            "Egress country check %d/%d: %s",
            attempt,
            ATTEMPTS,
            ", ".join(f"{name}={country}" for name, country in results.items())
            or "no valid responses",
        )
        if is_verified_japan(results):
            logging.info("Japanese egress verified by independent services")
            return 0
        if attempt < ATTEMPTS:
            time.sleep(RETRY_SECONDS)

    logging.error(
        "Japanese egress could not be verified; refusing to expose Google Play "
        "credentials or start APK acquisition"
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
