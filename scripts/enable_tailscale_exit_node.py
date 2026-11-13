"""Safely route this runner through a Tailscale exit node.

Why this exists
---------------
Setting ``tailscale set --exit-node=<ip>`` sends *all* of the runner's traffic
through that peer. If the peer is offline (for example a phone that went to
sleep), every connection black-holes, including the runner's own connection to
GitHub. GitHub then loses the runner heartbeat and the job ends with
"The operation was canceled." and no useful error.

This script therefore:
  1. resolves the exit node,
  2. pings the peer (retrying for up to a minute, because a freshly joined
     ephemeral node can take a while to become reachable) *before* it becomes
     the default route; on failure it logs diagnostics for troubleshooting,
  3. enables it, then proves the internet is still reachable through it,
  4. restores the default route on any failure.

Exit code 0 means the exit node is active and working. Any non-zero exit code
guarantees the default route has been restored.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from urllib.request import Request, urlopen

from scripts.resolve_tailscale_exit_node import (
    ExitNodeResolutionError,
    _advertised_exit_nodes,
    resolve_exit_node_ip,
)

PING_TIMEOUT = "5s"
PING_ATTEMPT_HARD_LIMIT_SECONDS = 20
PING_RETRY_WINDOW_SECONDS = 60
PING_RETRY_INTERVAL_SECONDS = 5
DIAGNOSTIC_HARD_LIMIT_SECONDS = 30
PROBE_URL = "https://api.github.com/zen"
PROBE_ATTEMPTS = 3
PROBE_TIMEOUT_SECONDS = 10
PROBE_RETRY_SECONDS = 3


def _log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def _run(args: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=timeout,
        check=False,
    )


def restore_default_route() -> None:
    try:
        _run(["sudo", "tailscale", "set", "--exit-node="], timeout=30)
        _log("Default route restored (exit node cleared).")
    except Exception as error:  # never mask the original failure
        _log(f"WARNING: could not clear exit node: {error}")


def _peer_for_ip(status: dict, ip: str) -> dict | None:
    for peer in _advertised_exit_nodes(status):
        if ip in [str(v) for v in peer.get("TailscaleIPs", [])]:
            return peer
    return None


def wait_for_peer(ip: str) -> bool:
    """Ping the peer until it answers or the retry window is exhausted."""
    deadline = time.monotonic() + PING_RETRY_WINDOW_SECONDS
    attempt = 0
    while True:
        attempt += 1
        try:
            ping = _run(
                ["tailscale", "ping", "-c", "1", f"--timeout={PING_TIMEOUT}", ip],
                timeout=PING_ATTEMPT_HARD_LIMIT_SECONDS,
            )
            if ping.returncode == 0:
                _log(f"Exit node {ip} answered ping (attempt {attempt}).")
                return True
            _log(f"Ping {attempt} to {ip} got no reply.")
        except subprocess.TimeoutExpired:
            _log(f"Ping {attempt} to {ip} timed out.")
        if time.monotonic() + PING_RETRY_INTERVAL_SECONDS >= deadline:
            return False
        time.sleep(PING_RETRY_INTERVAL_SECONDS)


def log_diagnostics(ip: str) -> None:
    """Print tailscale status and a verbose ping so the cause can be told apart
    (peer offline / relay only / no route / peer not yet known)."""
    commands = [
        ["tailscale", "status"],
        ["tailscale", "ping", "-c", "3", "--verbose", f"--timeout={PING_TIMEOUT}", ip],
    ]
    for command in commands:
        print(f"::group::Diagnostics: {' '.join(command)}", file=sys.stderr, flush=True)
        try:
            result = _run(command, timeout=DIAGNOSTIC_HARD_LIMIT_SECONDS)
            _log(f"exit code: {result.returncode}")
            if result.stdout.strip():
                _log(result.stdout.rstrip())
            if result.stderr.strip():
                _log(result.stderr.rstrip())
        except Exception as error:
            _log(f"diagnostic command failed: {error}")
        print("::endgroup::", file=sys.stderr, flush=True)


def internet_reachable() -> bool:
    for attempt in range(1, PROBE_ATTEMPTS + 1):
        try:
            request = Request(PROBE_URL, headers={"User-Agent": "Morphe-AutoBuilds/exit-node"})
            with urlopen(request, timeout=PROBE_TIMEOUT_SECONDS) as response:
                if 200 <= response.status < 400:
                    return True
        except Exception as error:
            _log(f"Connectivity probe {attempt}/{PROBE_ATTEMPTS} failed: {error}")
        if attempt < PROBE_ATTEMPTS:
            time.sleep(PROBE_RETRY_SECONDS)
    return False


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("exit_node", nargs="?", default="", help="hostname or Tailscale IP")
    parser.add_argument("--allow-lan-access", action="store_true")
    args = parser.parse_args(argv)

    try:
        status_result = _run(["tailscale", "status", "--json"], timeout=30)
        if status_result.returncode != 0:
            _log(f"tailscale status failed: {status_result.stderr.strip()}")
            return 1
        status = json.loads(status_result.stdout)
        ip = resolve_exit_node_ip(status, args.exit_node)
    except (json.JSONDecodeError, ExitNodeResolutionError, subprocess.TimeoutExpired) as error:
        _log(f"Tailscale exit node resolution failed: {error}")
        return 1

    peer = _peer_for_ip(status, ip)
    if peer is not None and peer.get("Online") is False:
        # The coordination server's view can lag right after joining, so this is
        # only a warning; the ping below is the authoritative check.
        _log(f"WARNING: exit node {ip} is reported offline; will keep pinging.")

    # Pre-flight: the peer must answer BEFORE it carries all traffic.
    if not wait_for_peer(ip):
        _log(f"Exit node {ip} is unreachable; not enabling it.")
        log_diagnostics(ip)
        return 1

    command = ["sudo", "tailscale", "set", f"--exit-node={ip}", "--accept-dns=false"]
    if args.allow_lan_access:
        command.append("--exit-node-allow-lan-access=true")
    try:
        result = _run(command, timeout=30)
    except subprocess.TimeoutExpired:
        restore_default_route()
        return 1
    if result.returncode != 0:
        _log(f"tailscale set failed: {result.stderr.strip()}")
        restore_default_route()
        return 1

    if not internet_reachable():
        _log("Internet is unreachable through the exit node; reverting.")
        restore_default_route()
        return 1

    _log(f"Exit node {ip} is active and reachable.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
