#!/usr/bin/env python3
"""Register a Google account with the CI-local gplaydl dispenser.

This is intended for a manually-dispatched GitHub Actions run. It starts an
empty JP-market dispenser, exposes it temporarily with a Cloudflare Quick
Tunnel, waits for the official gplaydl Authenticator to sync an account, links
the repository's existing GPLAYDL_API_KEY to that account, validates token
minting without printing credentials, and writes a doubly-encrypted DB snapshot.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import persisted_gplaydl_dispenser as dispenser

TUNNEL_PATTERN = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")
WAIT_SECONDS = int(os.getenv("GPLAYDL_REGISTRATION_WAIT_SECONDS", "900"))
SETTLE_SECONDS = int(os.getenv("GPLAYDL_REGISTRATION_SETTLE_SECONDS", "15"))


def _summary(text: str) -> None:
    path = os.getenv("GITHUB_STEP_SUMMARY", "").strip()
    if not path:
        return
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(text.rstrip() + "\n")


def _start_tunnel(local_url: str) -> tuple[subprocess.Popen[str], str, object, Path]:
    cloudflared = shutil.which("cloudflared")
    if not cloudflared:
        raise RuntimeError("cloudflared is required for Google Play account registration")

    log_path = Path(tempfile.mkdtemp(prefix="gplaydl-tunnel-")) / "cloudflared.log"
    log_handle = log_path.open("w", encoding="utf-8")
    process = subprocess.Popen(
        [cloudflared, "tunnel", "--no-autoupdate", "--url", local_url],
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        text=True,
    )

    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if process.poll() is not None:
            break
        log_handle.flush()
        text = log_path.read_text(encoding="utf-8", errors="replace")
        match = TUNNEL_PATTERN.search(text)
        if match:
            return process, match.group(0), log_handle, log_path
        time.sleep(0.5)

    tail = "\n".join(
        log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-30:]
    )
    process.terminate()
    log_handle.close()
    raise RuntimeError(f"Cloudflare Quick Tunnel did not become ready: {tail}")


def _wait_for_account() -> None:
    deadline = time.monotonic() + WAIT_SECONDS
    print("Waiting for the official gplaydl Authenticator to sync an account...", flush=True)
    while time.monotonic() < deadline:
        try:
            count = dispenser.registration_account_count()
        except Exception:
            count = 0
        if count > 0:
            print(f"Authenticator sync detected: {count} account(s).", flush=True)
            if SETTLE_SECONDS > 0:
                print(
                    f"Waiting {SETTLE_SECONDS}s for any additional account syncs...",
                    flush=True,
                )
                time.sleep(SETTLE_SECONDS)
            return
        time.sleep(2)
    raise RuntimeError(
        "Timed out waiting for Authenticator account sync. Re-run registration when ready."
    )


def main() -> int:
    if not os.getenv("GPLAYDL_API_KEY", "").strip():
        raise RuntimeError(
            "GPLAYDL_API_KEY repository secret is required. It is used to bind the "
            "CI-local API identity and derive snapshot encryption keys."
        )

    local_url = dispenser.start_registration_server()
    tunnel_process = None
    tunnel_handle = None
    tunnel_log = None
    try:
        tunnel_process, public_url, tunnel_handle, tunnel_log = _start_tunnel(local_url)

        print("=" * 72, flush=True)
        print(f"TEMPORARY AUTHENTICATOR SERVER: {public_url}", flush=True)
        print("Set this URL in the official gplaydl Authenticator, then add the account.", flush=True)
        print("=" * 72, flush=True)
        _summary(
            f"""
## Google Play account registration

Temporary Authenticator server:

`{public_url}`

On the phone, open the **official gplaydl Authenticator** and:

1. Open **Settings** and set the dispenser/server URL to the temporary URL above.
2. Accept/enrol the device if prompted.
3. Add the Japanese Google account you want CI to use.
4. Leave this Actions run open until it reports that encrypted state was saved.

The tunnel exists only for this workflow run. No AAS token is printed or copied.
"""
        )

        _wait_for_account()
        dispenser.attach_ci_api_key()
        diagnostics = dispenser.validate_registered_account()
        print(
            "Play token mint validated without exposing credentials: "
            f"mccmnc={diagnostics['mccmnc'] or 'unknown'} "
            f"locale={diagnostics['locale'] or 'unknown'}",
            flush=True,
        )

        output_dir = Path(os.getenv("GPLAYDL_STATE_OUTPUT", ".gplaydl-state"))
        output = output_dir / dispenser.STATE_FILENAME
        dispenser.snapshot_state(output)
        print(f"Encrypted Google Play dispenser state saved to {output}", flush=True)
        _summary(
            "\nRegistration completed. The encrypted dispenser state is ready to be "
            "saved in the GitHub Actions cache and restored by normal APK builds."
        )
        return 0
    finally:
        if tunnel_process is not None:
            tunnel_process.terminate()
            try:
                tunnel_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                tunnel_process.kill()
        if tunnel_handle is not None:
            tunnel_handle.close()
        if tunnel_log and tunnel_log.exists() and tunnel_process and tunnel_process.returncode not in (0, None, -15):
            tail = "\n".join(
                tunnel_log.read_text(encoding="utf-8", errors="replace").splitlines()[-20:]
            )
            print(f"cloudflared log tail:\n{tail}", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
