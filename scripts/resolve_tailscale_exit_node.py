"""Resolve a configured Tailscale exit node to an unambiguous tailnet IP."""

from __future__ import annotations

import json
import subprocess
import sys
from typing import Any


class ExitNodeResolutionError(RuntimeError):
    """No safe, unique advertised exit node could be selected."""


def _normalized_names(peer: dict[str, Any]) -> set[str]:
    names: set[str] = set()
    for key in ("HostName", "DNSName"):
        value = str(peer.get(key, "")).strip().rstrip(".").casefold()
        if not value:
            continue
        names.add(value)
        names.add(value.split(".", 1)[0])
    return names


def _advertised_exit_nodes(status: dict[str, Any]) -> list[dict[str, Any]]:
    peers = status.get("Peer", {})
    if not isinstance(peers, dict):
        return []
    return [
        peer
        for peer in peers.values()
        if isinstance(peer, dict) and peer.get("ExitNodeOption") is True
    ]


def resolve_exit_node_ip(status: dict[str, Any], requested: str) -> str:
    candidates = _advertised_exit_nodes(status)
    wanted = requested.strip().rstrip(".").casefold()
    matches = [
        peer
        for peer in candidates
        if wanted in _normalized_names(peer)
        or wanted in {
            str(value).strip().casefold()
            for value in peer.get("TailscaleIPs", [])
        }
    ]

    if len(matches) == 1:
        selected = matches[0]
    elif not matches and len(candidates) == 1:
        selected = candidates[0]
        hostname = str(selected.get("HostName", "unknown"))
        print(
            f"Configured exit node {requested!r} did not match; using the only "
            f"advertised exit node {hostname!r}",
            file=sys.stderr,
        )
    elif not matches:
        available = ", ".join(
            str(peer.get("HostName", "unknown")) for peer in candidates
        ) or "none"
        raise ExitNodeResolutionError(
            f"configured exit node {requested!r} was not found; "
            f"advertised exit nodes: {available}"
        )
    else:
        raise ExitNodeResolutionError(
            f"configured exit node {requested!r} matched multiple advertised peers"
        )

    addresses = [
        str(value).strip()
        for value in selected.get("TailscaleIPs", [])
        if str(value).strip()
    ]
    ipv4 = next((value for value in addresses if ":" not in value), None)
    if not ipv4:
        raise ExitNodeResolutionError("selected exit node has no Tailscale IPv4 address")
    return ipv4


def main(argv: list[str]) -> int:
    if len(argv) != 2 or not argv[1].strip():
        print("usage: resolve_tailscale_exit_node.py <hostname-or-ip>", file=sys.stderr)
        return 2
    try:
        output = subprocess.run(
            ["tailscale", "status", "--json"],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        ).stdout
        selected = resolve_exit_node_ip(json.loads(output), argv[1])
    except (subprocess.CalledProcessError, json.JSONDecodeError, ExitNodeResolutionError) as error:
        print(f"Tailscale exit node resolution failed: {error}", file=sys.stderr)
        return 1
    print(selected)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
