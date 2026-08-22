import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src import local_gplaydl_dispenser as local


CLIENT_GO = '''package gplay

import (
\t"net/url"
\t"strings"
)

func (c *Client) Mint(ctx context.Context, account Account, dc DeviceConfig, locale, proxyURL string) (*AuthBundle, error) {
\treturn &AuthBundle{
\t\tDeviceInfoProvider: DeviceInfoProvider{
\t\t\tMccMnc:              "310260",
\t\t},
\t}, nil
}

func (c *Client) exchangeAASToken() {
\tparams := url.Values{
\t\t"device_country":               {"IN"},
\t}
\t_ = params
}

func setDFEHeaders(req *http.Request) {
\th := req.Header
\th.Set("X-DFE-MCCMNC", "21601")
}
'''


class LocalGPlayDlDispenserTests(unittest.TestCase):
    def test_market_patch_is_generic_and_environment_driven(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            checkout = Path(directory)
            path = checkout / "internal" / "gplay" / "client.go"
            path.parent.mkdir(parents=True)
            path.write_text(CLIENT_GO, encoding="utf-8")

            local.patch_upstream_market(checkout)
            patched = path.read_text(encoding="utf-8")

            self.assertIn('envOr("GPLAY_DEVICE_COUNTRY", "IN")', patched)
            self.assertIn('envOr("GPLAY_MCCMNC", "310260")', patched)
            self.assertIn('envOr("GPLAY_MCCMNC", "21601")', patched)
            self.assertIn('envOr("GPLAY_DEFAULT_LOCALE", locale)', patched)
            self.assertIn('out["CellOperator"] = mccmnc[:3]', patched)
            self.assertIn('out["SimOperator"] = mccmnc[3:]', patched)
            self.assertIn('out["TimeZone"] = timezone', patched)
            self.assertNotIn("jp.japanpost", patched)

    def test_stale_upstream_patch_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            checkout = Path(directory)
            path = checkout / "internal" / "gplay" / "client.go"
            path.parent.mkdir(parents=True)
            path.write_text("package gplay\n", encoding="utf-8")

            with self.assertRaisesRegex(RuntimeError, "upstream gplaydl-dispenser changed"):
                local.patch_upstream_market(checkout)

    def test_no_aas_credentials_keeps_hosted_path(self) -> None:
        with mock.patch.dict(
            os.environ,
            {"GPLAY_EMAIL": "", "GPLAYDL_EMAIL": "", "GPLAY_AAS_TOKEN": ""},
            clear=False,
        ):
            self.assertIsNone(local._credentials())

    def test_partial_aas_credentials_fail_instead_of_silent_fallback(self) -> None:
        with mock.patch.dict(
            os.environ,
            {"GPLAY_EMAIL": "example@gmail.com", "GPLAY_AAS_TOKEN": ""},
            clear=False,
        ):
            with self.assertRaisesRegex(RuntimeError, "requires both"):
                local._credentials()

    def test_wait_postgres_final_ignores_init_time_temp_server(self) -> None:
        responses = [
            subprocess.CompletedProcess([], 0, "docker-entrypoint.sh\n", ""),
            subprocess.CompletedProcess([], 0, "postgres\n", ""),
            subprocess.CompletedProcess([], 0, "1\n", ""),
        ]
        with (
            mock.patch.object(local, "_run", side_effect=responses) as run,
            mock.patch.object(local.time, "sleep"),
        ):
            local._wait_postgres_final("db", timeout_seconds=1)

        commands = [call.args[0] for call in run.call_args_list]
        self.assertEqual(commands[0], ["docker", "exec", "db", "cat", "/proc/1/comm"])
        self.assertEqual(commands[1], ["docker", "exec", "db", "cat", "/proc/1/comm"])
        self.assertIn("psql", commands[2])
        self.assertIn("SELECT 1;", commands[2])
        self.assertFalse(any("pg_isready" in command for command in commands))


if __name__ == "__main__":
    unittest.main()
