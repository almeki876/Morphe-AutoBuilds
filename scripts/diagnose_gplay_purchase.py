#!/usr/bin/env python3
"""Run the current gplaydl purchase diagnostic against the ephemeral dispenser."""

from __future__ import annotations

import os
import re

from src import gplaydl_purchase_diagnostics
from src import local_gplaydl_dispenser


def main() -> int:
    raw_packages = os.getenv("GPLAY_DIAGNOSTIC_PACKAGE", "").strip()
    version_code = os.getenv("GPLAY_DIAGNOSTIC_VERSION_CODE", "").strip() or None
    packages = [item.strip() for item in re.split(r"[,\s]+", raw_packages) if item.strip()]
    if not packages or any("." not in package for package in packages):
        raise RuntimeError(
            "GPLAY_DIAGNOSTIC_PACKAGE must contain one or more package names"
        )
    if version_code and len(packages) != 1:
        raise RuntimeError(
            "GPLAY_DIAGNOSTIC_VERSION_CODE can only be used with one package"
        )

    if not local_gplaydl_dispenser.ensure_running():
        raise RuntimeError("GPLAY_EMAIL and GPLAY_AAS_TOKEN are required for diagnostics")

    for package in packages:
        gplaydl_purchase_diagnostics.diagnose_purchase_failure(package, version_code)
        gplaydl_purchase_diagnostics.diagnose_priority_profiles(package, version_code)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
