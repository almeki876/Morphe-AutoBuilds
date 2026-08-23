import unittest

from scripts.resolve_tailscale_exit_node import (
    ExitNodeResolutionError,
    resolve_exit_node_ip,
)


def _peer(name: str, ip: str, *, exit_node: bool = True) -> dict:
    return {
        "HostName": name,
        "DNSName": f"{name}.example.ts.net.",
        "TailscaleIPs": [ip, "fd7a:115c:a1e0::1"],
        "ExitNodeOption": exit_node,
    }


class ResolveTailscaleExitNodeTests(unittest.TestCase):
    def test_resolves_exact_hostname_to_ipv4(self) -> None:
        status = {"Peer": {"node": _peer("galaxy-a21-1", "100.64.0.10")}}
        self.assertEqual(
            resolve_exit_node_ip(status, "galaxy-a21-1"),
            "100.64.0.10",
        )

    def test_resolves_full_magicdns_name(self) -> None:
        status = {"Peer": {"node": _peer("galaxy-a21-1", "100.64.0.10")}}
        self.assertEqual(
            resolve_exit_node_ip(status, "galaxy-a21-1.example.ts.net"),
            "100.64.0.10",
        )

    def test_uses_only_advertised_exit_node_when_label_is_stale(self) -> None:
        status = {"Peer": {"node": _peer("pixel-jp", "100.64.0.11")}}
        self.assertEqual(
            resolve_exit_node_ip(status, "old-name"),
            "100.64.0.11",
        )

    def test_refuses_ambiguous_fallback(self) -> None:
        status = {
            "Peer": {
                "one": _peer("pixel-jp", "100.64.0.11"),
                "two": _peer("nas-jp", "100.64.0.12"),
            }
        }
        with self.assertRaises(ExitNodeResolutionError):
            resolve_exit_node_ip(status, "old-name")

    def test_ignores_peers_not_advertising_exit_node(self) -> None:
        status = {
            "Peer": {
                "ordinary": _peer("galaxy-a21-1", "100.64.0.10", exit_node=False)
            }
        }
        with self.assertRaises(ExitNodeResolutionError):
            resolve_exit_node_ip(status, "galaxy-a21-1")


if __name__ == "__main__":
    unittest.main()
