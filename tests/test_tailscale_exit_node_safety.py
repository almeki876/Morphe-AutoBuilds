from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

from scripts import enable_tailscale_exit_node as mod

STATUS = {
    "Peer": {
        "a": {
            "HostName": "galaxy-a21-1",
            "ExitNodeOption": True,
            "Online": True,
            "TailscaleIPs": ["100.114.139.27"],
        }
    }
}


def _cp(code=0, out="", err=""):
    return mock.Mock(returncode=code, stdout=out, stderr=err)


class EnableExitNodeTests(unittest.TestCase):
    def _run(self, calls):
        seen = []

        def fake_run(args, timeout):
            seen.append(args)
            return calls(args)

        with mock.patch.object(mod, "_run", fake_run):
            return seen

    def test_unreachable_peer_is_never_set_as_exit_node(self) -> None:
        import json

        seen = []

        def fake_run(args, timeout):
            seen.append(args)
            if args[:3] == ["tailscale", "status", "--json"]:
                return _cp(0, json.dumps(STATUS))
            if args[:2] == ["tailscale", "ping"]:
                return _cp(1, "no reply")
            return _cp(0)

        with mock.patch.object(mod, "_run", fake_run), mock.patch.object(
            mod, "PING_RETRY_WINDOW_SECONDS", 0
        ), mock.patch.object(mod.time, "sleep"):
            self.assertEqual(mod.main(["galaxy-a21-1"]), 1)
        self.assertFalse(any("set" in a and any("--exit-node=1" in x for x in a) for a in seen))
        # diagnostics are logged on failure
        self.assertIn(["tailscale", "status"], seen)
        self.assertTrue(any("--verbose" in a for a in seen))

    def test_ping_is_retried_until_peer_answers(self) -> None:
        import json

        pings = []

        def fake_run(args, timeout):
            if args[:3] == ["tailscale", "status", "--json"]:
                return _cp(0, json.dumps(STATUS))
            if args[:2] == ["tailscale", "ping"]:
                pings.append(args)
                return _cp(0 if len(pings) >= 3 else 1)
            return _cp(0)

        with mock.patch.object(mod, "_run", fake_run), mock.patch.object(
            mod, "internet_reachable", return_value=True
        ), mock.patch.object(mod.time, "sleep"):
            self.assertEqual(mod.main(["galaxy-a21-1"]), 0)
        self.assertEqual(len(pings), 3)

    def test_route_is_restored_when_internet_is_dead_after_enable(self) -> None:
        import json

        seen = []

        def fake_run(args, timeout):
            seen.append(args)
            if args[:3] == ["tailscale", "status", "--json"]:
                return _cp(0, json.dumps(STATUS))
            return _cp(0)

        with mock.patch.object(mod, "_run", fake_run), mock.patch.object(
            mod, "internet_reachable", return_value=False
        ):
            self.assertEqual(mod.main(["galaxy-a21-1"]), 1)
        self.assertIn(["sudo", "tailscale", "set", "--exit-node="], seen)

    def test_success_keeps_exit_node(self) -> None:
        import json

        def fake_run(args, timeout):
            if args[:3] == ["tailscale", "status", "--json"]:
                return _cp(0, json.dumps(STATUS))
            return _cp(0)

        with mock.patch.object(mod, "_run", fake_run), mock.patch.object(
            mod, "internet_reachable", return_value=True
        ):
            self.assertEqual(mod.main(["galaxy-a21-1"]), 0)


class WorkflowCancellationTests(unittest.TestCase):
    def test_build_workflow_does_not_cancel_running_builds(self) -> None:
        text = Path(".github/workflows/build.yml").read_text(encoding="utf-8")
        import re

        self.assertIsNone(
            re.search(r"^\s*cancel-in-progress:\s*true", text, re.MULTILINE)
        )

    def test_exit_node_is_always_restored(self) -> None:
        ops = Path(".github/workflows/operations.yml").read_text(encoding="utf-8")
        build = Path(".github/workflows/build.yml").read_text(encoding="utf-8")
        self.assertIn("scripts.enable_tailscale_exit_node", ops)
        self.assertIn("scripts.enable_tailscale_exit_node", build)
        self.assertNotIn("tailscale set --exit-node=\"$exit_node_ip\"", ops + build)
        revert = ops.split("- name: Return to Default Route if Egress Check Fails", 1)[1]
        self.assertIn("always()", revert.split("run:", 1)[0])


if __name__ == "__main__":
    unittest.main()
