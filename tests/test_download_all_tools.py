import pathlib
import subprocess
import tempfile
import unittest
from unittest import mock

from scripts import download_all_tools


class DownloadAssetGhTests(unittest.TestCase):
    def test_bad_pat_is_not_retried_and_falls_back(self):
        calls = []

        def fake_run(command, **kwargs):
            calls.append(kwargs["env"]["GH_TOKEN"])
            if len(calls) == 1:
                return subprocess.CompletedProcess(
                    command,
                    1,
                    stdout="",
                    stderr='HTTP 401: Bad credentials (https://api.github.com/)',
                )

            target_dir = pathlib.Path(command[command.index("--dir") + 1])
            (target_dir / "patches.rvp").write_bytes(b"valid patch")
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

        with tempfile.TemporaryDirectory() as temp_dir:
            destination = pathlib.Path(temp_dir) / "patches.rvp"
            with mock.patch.object(
                download_all_tools.subprocess, "run", side_effect=fake_run
            ), mock.patch.object(download_all_tools.time, "sleep") as sleep:
                result = download_all_tools.download_asset_gh(
                    "owner/private",
                    "v1",
                    "patches.rvp",
                    destination,
                    token="expired-pat",
                    fallback_token="workflow-token",
                )

            self.assertTrue(result)
            self.assertEqual(calls, ["expired-pat", "workflow-token"])
            self.assertEqual(destination.read_bytes(), b"valid patch")
            sleep.assert_not_called()

    def test_permanent_error_is_not_retried(self):
        completed = subprocess.CompletedProcess(
            ["gh"],
            1,
            stdout="",
            stderr="HTTP 403: Resource not accessible by integration",
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            destination = pathlib.Path(temp_dir) / "patches.rvp"
            with mock.patch.object(
                download_all_tools.subprocess, "run", return_value=completed
            ) as run, mock.patch.object(download_all_tools.time, "sleep") as sleep:
                result = download_all_tools.download_asset_gh(
                    "owner/private",
                    "v1",
                    "patches.rvp",
                    destination,
                    token="no-access",
                )

            self.assertFalse(result)
            self.assertEqual(run.call_count, 1)
            self.assertFalse(destination.exists())
            sleep.assert_not_called()

    def test_missing_credentials_fails_without_invoking_gh(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            destination = pathlib.Path(temp_dir) / "patches.rvp"
            with mock.patch.object(
                download_all_tools.subprocess, "run"
            ) as run:
                result = download_all_tools.download_asset_gh(
                    "owner/private",
                    "v1",
                    "patches.rvp",
                    destination,
                    token="",
                    fallback_token="",
                )

            self.assertFalse(result)
            run.assert_not_called()


class GitHubErrorClassificationTests(unittest.TestCase):
    def test_auth_and_permission_errors_are_permanent(self):
        for message in (
            "401 Unauthorized: Bad credentials",
            "HTTP 403: Resource not accessible by integration",
            "HTTP 404: Not Found",
        ):
            with self.subTest(message=message):
                self.assertTrue(
                    download_all_tools._is_permanent_github_error(message)
                )

    def test_server_error_is_retryable(self):
        self.assertFalse(
            download_all_tools._is_permanent_github_error(
                "HTTP 502: upstream temporarily unavailable"
            )
        )


if __name__ == "__main__":
    unittest.main()
