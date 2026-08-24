"""Authenticated Google Play downloads with progress-aware fallback handling.

Google Play remains the preferred APK origin for every app except packages that
are explicitly GitHub-only. Current releases try pinned gplaydl first using the
already configured account/dispenser, then playfetch, then apkeep. A final
fresh-device gplaydl retry remains available as the expensive safety net for
region/device restricted apps. Exact versionCodes use gplaydl first and only
accept a current-release fallback when its manifest exactly matches the
requested candidate.

Transfer commands are not killed merely because a fixed wall-clock duration has
elapsed. Instead, this module watches the actual download directory. A client
is abandoned only when no payload starts within a bounded startup window or when
a started payload stops changing for the configured idle window. Short metadata
and version commands still use ordinary command timeouts.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from src import archive_stability, apk_identity, local_gplaydl_dispenser, play_version_resolver
from src.versioning import VersionCandidate

OFFICIAL_GPLAYDL_COMMAND = "gplaydl"
OFFICIAL_APKEEP_COMMAND = "apkeep"
OFFICIAL_PLAYFETCH_COMMAND = "playfetch"
SUPPORTED_APKEEP_VERSION = "1.0.0"
SUPPORTED_PLAYFETCH_VERSION = "v0.9.1"
APKEEP_GOOGLE_PLAY_OPTIONS = (
    "device=px_9a,locale=ja_JP,timezone=Asia/Tokyo,split_apk=true"
)

# gplaydl must request both canonical English metadata and Japanese resources.
# Keep this policy local to the command builder so it cannot depend on global
# namespace side effects (for example builtins) or import-order behavior.
DEFAULT_GPLAYDL_LOCALES = "en-US,ja"

GITHUB_ONLY_PACKAGES = frozenset({"com.adguard.android"})
_COMMAND_CAPS = {
    "version": 8.0,
    "playfetch": 45.0,
    "apkeep": 45.0,
    "gplaydl": 45.0,
    "generic": 45.0,
}
_TRANSFER_START_TIMEOUT_SECONDS = 90.0
_TRANSFER_IDLE_TIMEOUT_SECONDS = 60.0