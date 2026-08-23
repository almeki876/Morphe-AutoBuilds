"""Failure-only diagnostics for the current upstream gplaydl purchase flow.

This module does not replace or alter gplaydl purchase/delivery behavior. It
replays the currently installed gplaydl package's own details/purchase calls
after an acquisition failure and logs only non-secret structural diagnostics.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Iterable

from src.gplaydl_profile_retry import ordered_priority_profiles

_SECRETISH = re.compile(r"^[A-Za-z0-9_./+=-]{48,}$")
_EMAIL = re.compile(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+")
_URL = re.compile(r"https?://\S+", re.IGNORECASE)
_AAS_TOKEN = re.compile(r"aas_et/[A-Za-z0-9_./+=-]+", re.IGNORECASE)
_BEARER = re.compile(r"bearer\s+[A-Za-z0-9_./+=-]+", re.IGNORECASE)
_LONG_TOKEN = re.compile(r"(?<![A-Za-z0-9_./+=-])[A-Za-z0-9_./+=-]{48,}(?![A-Za-z0-9_./+=-])")


def _safe_human_strings(values: Iterable[str], limit: int = 8) -> list[str]:
    """Keep short human-readable protobuf strings while rejecting credentials."""
    result: list[str] = []
    for value in values:
        text = str(value).strip()
        lower = text.lower()
        if not text or len(text) > 180:
            continue
        if "@" in text or "http://" in lower or "https://" in lower:
            continue
        if "aas_et/" in lower or lower.startswith("bearer "):
            continue
        if _SECRETISH.fullmatch(text):
            continue
        # Diagnostic errors/messages are normally prose. Avoid dumping opaque
        # protobuf identifiers even when they happen to be short.
        if not any(ch.isspace() for ch in text):
            continue
        if text not in result:
            result.append(text)
        if len(result) >= limit:
            break
    return result


def _safe_response_preview(content: bytes, limit: int = 300) -> str:
    """Return a compact, redacted preview of a non-success HTTP response body."""
    if not content:
        return "none"

    text = content.decode("utf-8", errors="replace")
    text = " ".join(text.split())
    if not text:
        return "none"

    text = _AAS_TOKEN.sub("[redacted-aas-token]", text)
    text = _BEARER.sub("[redacted-bearer]", text)
    text = _EMAIL.sub("[redacted-email]", text)
    text = _URL.sub("[redacted-url]", text)
    text = _LONG_TOKEN.sub("[redacted-token]", text)

    if len(text) > limit:
        text = text[:limit] + "…"
    return text


def diagnose_purchase_failure(package: str, version_code: str | None = None) -> None:
    """Log a secret-safe snapshot of current gplaydl's free-app purchase call.

    The normal download already failed before this function is called. We use
    the currently installed gplaydl package and the same ephemeral dispenser;
    no alternate or legacy Play implementation is involved.
    """
    import gplaydl.api as api
    import gplaydl.auth as auth
    from gplaydl.protobuf import extract_strings

    dispenser = os.getenv("GPLAYDL_DISPENSER_URL", "").strip() or None
    email = (
        os.getenv("GPLAYDL_EMAIL", "").strip()
        or os.getenv("GPLAY_EMAIL", "").strip()
        or None
    )
    arch = os.getenv("GPLAYDL_ARCH", "arm64").strip() or "arm64"

    auth_data = auth.ensure_auth(
        arch=arch,
        dispenser_url=dispenser,
        force_refresh=True,
        email=email,
    )
    if not auth_data:
        logging.warning(
            "gplaydl purchase diagnostic: could not obtain a fresh upstream auth token"
        )
        return

    details = api.get_details(package, auth_data)
    vc = int(version_code) if version_code else int(details.version_code)

    observed: dict[str, object] = {}
    original_post = api.httpx.post

    def recording_post(url, *args, **kwargs):
        response = original_post(url, *args, **kwargs)
        if str(url) == api.PURCHASE_URL:
            observed["status"] = response.status_code
            observed["content"] = bytes(response.content)
            observed["content_type"] = response.headers.get("content-type", "")
        return response

    api.httpx.post = recording_post
    try:
        delivery_token = api.purchase(package, vc, auth_data)
    finally:
        api.httpx.post = original_post

    content = observed.get("content")
    messages: list[str] = []
    response_bytes = 0
    response_preview = "none"
    if isinstance(content, bytes):
        response_bytes = len(content)
        response_preview = _safe_response_preview(content)
        if content:
            try:
                messages = _safe_human_strings(extract_strings(content))
            except Exception:  # diagnostic parsing must never replace the real error
                messages = []

    headers = api.build_headers(auth_data)
    logging.error(
        "🧪 gplaydl purchase diagnostic: http=%s delivery_token=%s "
        "details_versionCode=%s mccmnc=%s accept_language=%s "
        "user_languages=%s response_content_type=%s response_bytes=%s "
        "response_preview=%s messages=%s",
        observed.get("status", "unknown"),
        bool(delivery_token),
        vc,
        headers.get("X-DFE-MCCMNC", ""),
        headers.get("Accept-Language", ""),
        headers.get("X-DFE-UserLanguages", ""),
        observed.get("content_type", "unknown"),
        response_bytes,
        response_preview,
        messages or "none",
    )


def _profile_token(value: object) -> str:
    """Keep bundled profile labels compact and unambiguous in CI logs."""
    text = str(value).strip()
    return re.sub(r"[^A-Za-z0-9._-]+", "_", text) or "unknown"


def diagnose_priority_profiles(
    package: str,
    version_code: str | None = None,
) -> list[dict[str, object]]:
    """Probe acquisition with a fresh token for every upstream priority profile.

    Only public profile metadata, HTTP/status classifications, versionCode, and
    success booleans are logged.  Account email, GSF IDs, auth tokens, dispenser
    URLs, response bodies, and exception messages are deliberately excluded.
    """
    import gplaydl.api as api
    import gplaydl.auth as auth
    from gplaydl.profiles import get_priority_profiles

    dispenser = os.getenv("GPLAYDL_DISPENSER_URL", "").strip() or None
    email = (
        os.getenv("GPLAYDL_EMAIL", "").strip()
        or os.getenv("GPLAY_EMAIL", "").strip()
        or None
    )
    arch = os.getenv("GPLAYDL_ARCH", "arm64").strip() or "arm64"
    results: list[dict[str, object]] = []

    # Do not apply the production preferred-profile override here: diagnostics
    # must enumerate upstream's complete, canonical priority order every time.
    profiles = ordered_priority_profiles(get_priority_profiles(arch))
    for key, profile in profiles:
        result: dict[str, object] = {
            "profile_key": key,
            "profile_name": str(profile.get("UserReadableName", key)),
            "token_minted": False,
            "details_http": "not-run",
            "details_version_code": "unknown",
            "purchase_http": "not-run",
            "delivery_token": False,
            "delivery_http": "not-run",
            "delivery_status": "not-run",
            "success": False,
            "failure_kind": "none",
        }

        try:
            # The local dispenser performs check-in inside every mint, yielding
            # a new GSF ID/token pair for this exact bundled profile.
            auth_data = auth.fetch_token_for_profile(
                profile,
                dispenser_url=dispenser,
                email=email,
            )
        except Exception as error:  # diagnostic must continue to later profiles
            result["failure_kind"] = type(error).__name__
            results.append(result)
            _log_profile_result(package, result)
            continue

        if not auth_data:
            result["failure_kind"] = "TokenUnavailable"
            results.append(result)
            _log_profile_result(package, result)
            continue
        result["token_minted"] = True

        original_get = api.httpx.get
        original_post = api.httpx.post

        def recording_get(url, *args, **kwargs):
            response = original_get(url, *args, **kwargs)
            target = str(url)
            if target.startswith(api.DETAILS_URL):
                result["details_http"] = response.status_code
            elif target.startswith(api.DELIVERY_URL):
                result["delivery_http"] = response.status_code
            return response

        def recording_post(url, *args, **kwargs):
            response = original_post(url, *args, **kwargs)
            if str(url) == api.PURCHASE_URL:
                result["purchase_http"] = response.status_code
            return response

        api.httpx.get = recording_get
        api.httpx.post = recording_post
        try:
            details = api.get_details(package, auth_data)
            vc = int(version_code) if version_code else int(details.version_code)
            result["details_version_code"] = vc
            delivery_token = api.purchase(package, vc, auth_data)
            result["delivery_token"] = bool(delivery_token)
            delivery = api.get_delivery(
                package,
                vc,
                auth_data,
                delivery_token,
            )
            result["success"] = bool(delivery.download_url)
            result["delivery_status"] = 1 if delivery.download_url else "empty"
        except api.AppNotPurchasedError:
            result["delivery_status"] = 3
            result["failure_kind"] = "AppNotPurchasedError"
        except api.AppNotSupportedError:
            result["delivery_status"] = 2
            result["failure_kind"] = "AppNotSupportedError"
        except api.AppNotAvailableError:
            result["failure_kind"] = "AppNotAvailableError"
        except api.PlayAPIError as error:
            result["failure_kind"] = type(error).__name__
        except Exception as error:  # preserve the matrix without leaking text
            result["failure_kind"] = type(error).__name__
        finally:
            api.httpx.get = original_get
            api.httpx.post = original_post

        results.append(result)
        _log_profile_result(package, result)

    successful = [str(item["profile_key"]) for item in results if item["success"]]
    logging.error(
        "🧪 gplaydl profile diagnostic summary: package=%s profiles=%s "
        "successes=%s preferred_profile=%s",
        package,
        len(results),
        len(successful),
        successful[0] if successful else "none",
    )
    return results


def _log_profile_result(package: str, result: dict[str, object]) -> None:
    logging.error(
        "🧪 gplaydl profile diagnostic: package=%s profile_key=%s "
        "profile_name=%s token_minted=%s details_http=%s "
        "details_versionCode=%s purchase_http=%s delivery_token=%s "
        "delivery_http=%s delivery_status=%s success=%s failure_kind=%s",
        package,
        _profile_token(result["profile_key"]),
        _profile_token(result["profile_name"]),
        str(bool(result["token_minted"])).lower(),
        result["details_http"],
        result["details_version_code"],
        result["purchase_http"],
        str(bool(result["delivery_token"])).lower(),
        result["delivery_http"],
        result["delivery_status"],
        str(bool(result["success"])).lower(),
        _profile_token(result["failure_kind"]),
    )
