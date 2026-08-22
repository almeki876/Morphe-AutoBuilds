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

_SECRETISH = re.compile(r"^[A-Za-z0-9_./+=-]{48,}$")


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
        return response

    api.httpx.post = recording_post
    try:
        delivery_token = api.purchase(package, vc, auth_data)
    finally:
        api.httpx.post = original_post

    content = observed.get("content")
    messages: list[str] = []
    if isinstance(content, bytes) and content:
        try:
            messages = _safe_human_strings(extract_strings(content))
        except Exception:  # diagnostic parsing must never replace the real error
            messages = []

    headers = api.build_headers(auth_data)
    logging.error(
        "🧪 gplaydl purchase diagnostic: http=%s delivery_token=%s "
        "details_versionCode=%s mccmnc=%s accept_language=%s "
        "user_languages=%s messages=%s",
        observed.get("status", "unknown"),
        bool(delivery_token),
        vc,
        headers.get("X-DFE-MCCMNC", ""),
        headers.get("Accept-Language", ""),
        headers.get("X-DFE-UserLanguages", ""),
        messages or "none",
    )
