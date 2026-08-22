#!/usr/bin/env python3
"""Run the current gplaydl purchase diagnostic against the ephemeral dispenser."""

from __future__ import annotations

import os

from src import gplaydl_purchase_diagnostics
from src import local_gplaydl_dispenser


def main() -> int:
    package = os.getenv("GPLAY_DIAGNOSTIC_PACKAGE", "").strip()
    version_code = os.getenv("GPLAY_DIAGNOSTIC_VERSION_CODE", "").strip() or None
    if not package or "." not in package:
        raise RuntimeError("GPLAY_DIAGNOSTIC_PACKAGE must be a package name")

    if not local_gplaydl_dispenser.ensure_running():
        raise RuntimeError("GPLAY_EMAIL and GPLAY_AAS_TOKEN are required for diagnostics")

    gplaydl_purchase_diagnostics.diagnose_purchase_failure(package, version_code)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
