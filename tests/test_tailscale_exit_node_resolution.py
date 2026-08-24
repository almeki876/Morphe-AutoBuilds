import unittest

from scripts.resolve_tailscale_exit_node import (
    ExitNodeResolutionError,
    resolve_exit_node_ip,
)


class TailscaleExitNodeResolutionTests(unittest.TestCase):
    def test_blank_config_uses_only_advertised_exit_node(self) -> None:
        status = {
            "Peer": {
                "node-key": {
                    "HostName": "jp-exit",
                    "DNSName": "jp-exit.example.ts.net.",
                    "ExitNodeOption": True,
                    "TailscaleIPs": ["100.64.0.10", "fd7a:115c:a1e0::10"],
                }
            }
        }
        self.assertEqual(resolve_exit_node_ip(status, ""), "100.64.0.10")

    def test_blank_config_refuses_ambiguous_exit_nodes(self) -> None:
        status = {
            "Peer": {
                "one": {
                    "HostName": "jp-one",
                    "ExitNodeOption": True,
                    "TailscaleIPs": ["100.64.0.10"],
                },
                "two": {
                    "HostName": "jp-two",
                    "ExitNodeOption": True,
                    "TailscaleIPs": ["100.64.0.11"],
                },
            }
        }
        with self.assertRaises(ExitNodeResolutionError):
            resolve_exit_node_ip(status, "")


if __name__ == "__main__":
    unittest.main()
