"""Run the final provider-only APK rescue after clearing any Tailscale exit node."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

from scripts.run_logged import run_logged


def clear_tailscale_exit_node() -> None:
    tailscale = shutil.which("tailscale")
    if not tailscale:
        return
    subprocess.run(
        ["sudo", tailscale, "set", "--exit-node="],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def main() -> int:
    clear_tailscale_exit_node()
    return run_logged(
        [sys.executable, "scripts/download_apks.py"],
        Path("download-provider-rescue.log"),
        unset_env=("GITHUB_ACTIONS",),
    )


if __name__ == "__main__":
    raise SystemExit(main())
