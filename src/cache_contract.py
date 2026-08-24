"""Process-wide APK cache contract guard.

The workflow still carries legacy BASE_APK_CACHE_TAG values in older callers.
The cache namespace is part of the input contract, not a user-tunable provider
setting, so a legacy tag must never be allowed to select an incompatible cache.
"""

from __future__ import annotations

import os


CACHE_CONTRACT_TAG = "base-apk-cache-v4-ja-jp-px9a-split"


def enforce() -> None:
    """Force the only cache namespace accepted by the current build contract."""
    requested = os.getenv("BASE_APK_CACHE_TAG", "")
    if requested != CACHE_CONTRACT_TAG:
        if requested:
            # Keep the diagnostic visible; silently accepting v2/v3 is exactly
            # what allowed a previously English payload to recur.
            print(
                "⚠️  Ignoring legacy BASE_APK_CACHE_TAG="
                f"{requested!r}; using {CACHE_CONTRACT_TAG!r}"
            )
        os.environ["BASE_APK_CACHE_TAG"] = CACHE_CONTRACT_TAG


# Enforce at import time so every Python entry point gets the same cache
# namespace before src.apk_cache evaluates CACHE_TAG.
enforce()
