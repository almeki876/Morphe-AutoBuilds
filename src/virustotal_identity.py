"""Resolve Android release identity from an already-known file SHA-256.

VirusTotal's file report exposes manifest-derived package, versionName and
versionCode through its Androguard attributes.  This module never uploads a
file: it only looks up a SHA-256 already published by an exact provider page.
"""

from __future__ import annotations

import logging
import os
import re

from src import utils
from src.versioning import VersionCandidate


_API_BASE = "https://www.virustotal.com/api/v3"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_MAX_BUNDLED_FILES = 40


def _api_get(path: str):
    api_key = os.getenv("VIRUSTOTAL_API_KEY", "").strip()
    if not api_key:
        return None
    return utils.cf_aware_get(
        f"{_API_BASE}{path}",
        headers={"x-apikey": api_key, "Accept": "application/json"},
        timeout=30,
        retries=2,
    )


def _objects(payload: object) -> list[dict]:
    if not isinstance(payload, dict):
        return []
    data = payload.get("data")
    if isinstance(data, dict):
        return [data]
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    return []


def _androguard_identity(
    file_object: dict,
    expected_package: str,
) -> VersionCandidate | None:
    attributes = file_object.get("attributes", {})
    androguard = attributes.get("androguard", {}) if isinstance(attributes, dict) else {}
    if not isinstance(androguard, dict):
        return None
    package = str(androguard.get("Package") or "").strip()
    name = str(androguard.get("AndroidVersionName") or "").strip()
    code = str(androguard.get("AndroidVersionCode") or "").strip()
    if package != expected_package or not name or not code.isdigit():
        return None
    try:
        return VersionCandidate(name=name, code=code)
    except ValueError:
        return None


def identities_for_sha256(
    sha256: str,
    expected_package: str,
) -> list[VersionCandidate]:
    """Return manifest identities from a VT file or its bundled APKs."""
    digest = str(sha256 or "").strip().casefold()
    if not _SHA256_RE.fullmatch(digest) or not expected_package:
        return []
    if not os.getenv("VIRUSTOTAL_API_KEY", "").strip():
        return []

    identities: list[VersionCandidate] = []
    seen: set[tuple[str, str]] = set()

    def add_objects(payload: object) -> None:
        for file_object in _objects(payload):
            identity = _androguard_identity(file_object, expected_package)
            if identity is None:
                continue
            key = (identity.name, identity.code or "")
            if key not in seen:
                identities.append(identity)
                seen.add(key)

    try:
        response = _api_get(f"/files/{digest}")
        if response is None:
            return []
        logging.info(
            "VirusTotal exact-hash metadata status=%s package=%s",
            response.status_code,
            expected_package,
        )
        if response.status_code != 200:
            return []
        add_objects(response.json())

        # XAPK/APKM files are ZIP containers. VirusTotal exposes their member
        # APK objects through this public relationship, including Androguard
        # attributes when those members have already been analysed.
        bundled = _api_get(
            f"/files/{digest}/bundled_files?limit={_MAX_BUNDLED_FILES}"
        )
        if bundled is not None:
            logging.info(
                "VirusTotal bundled-file metadata status=%s package=%s",
                bundled.status_code,
                expected_package,
            )
            if bundled.status_code == 200:
                add_objects(bundled.json())
    except Exception as error:
        logging.info(
            "VirusTotal exact-hash identity lookup failed for %s: %s",
            expected_package,
            type(error).__name__,
        )
        return identities

    return identities
