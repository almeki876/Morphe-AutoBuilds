import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import download_apks
from src.versioning import VersionCandidate


class SmartDownloadRoutingTests(unittest.TestCase):
    def _metadata(self, root: str, app: str, **values) -> None:
        path = Path(root) / "app-metadata"
        path.mkdir(parents=True, exist_ok=True)
        payload = {"package": "example.package", **values}
        (path / f"{app}.json").write_text(json.dumps(payload), encoding="utf-8")

    def test_auto_policy_does_not_handoff_before_providers(self) -> None:
        with tempfile.TemporaryDirectory() as directory, mock.patch.dict(
            os.environ,
            {"GITHUB_ACTIONS": "true", "APP_NAME": "example"},
            clear=False,
        ):
            self._metadata(directory, "example", source_policy="provider-chain")
            old = Path.cwd()
            os.chdir(directory)
            try:
                with mock.patch.object(download_apks, "_tailscale_fallback_active") as active:
                    download_apks._request_japan_first_handoff("example")
                active.assert_not_called()
            finally:
                os.chdir(old)

    def test_japan_first_requests_tailscale_before_network(self) -> None:
        with tempfile.TemporaryDirectory() as directory, mock.patch.dict(
            os.environ,
            {"GITHUB_ACTIONS": "true", "APP_NAME": "example"},
            clear=False,
        ):
            self._metadata(
                directory,
                "example",
                source_policy="provider-chain",
                egress_policy="japan-first",
            )
            old = Path.cwd()
            os.chdir(directory)
            try:
                with mock.patch.object(download_apks, "_tailscale_fallback_active", return_value=False):
                    with self.assertRaisesRegex(RuntimeError, "japan-first"):
                        download_apks._request_japan_first_handoff("example")
            finally:
                os.chdir(old)

    def test_japan_first_continues_when_tailscale_is_active(self) -> None:
        with tempfile.TemporaryDirectory() as directory, mock.patch.dict(
            os.environ,
            {"GITHUB_ACTIONS": "true", "APP_NAME": "example"},
            clear=False,
        ):
            self._metadata(
                directory,
                "example",
                source_policy="provider-chain",
                egress_policy="japan-first",
            )
            old = Path.cwd()
            os.chdir(directory)
            try:
                with mock.patch.object(download_apks, "_tailscale_fallback_active", return_value=True):
                    download_apks._request_japan_first_handoff("example")
            finally:
                os.chdir(old)

    def test_exact_cache_hit_short_circuits_network_origins(self) -> None:
        candidate = VersionCandidate(name="1.2.3", code="123")
        cached = Path("cached-example-v1.2.3.apk")
        with mock.patch.object(download_apks.apk_cache, "restore", return_value=cached), \
             mock.patch.object(download_apks, "_validate_downloaded_identity"), \
             mock.patch.object(download_apks, "_record_cached_download"):
            result = download_apks._restore_cached_candidate(
                "example", "example.package", "arm64-v8a", [candidate]
            )
        self.assertEqual(result, (cached, "1.2.3"))


if __name__ == "__main__":
    unittest.main()
