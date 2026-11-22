"""Tests for tool download failure issue reporting."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import report_tool_download_failures


class ReportToolDownloadFailuresTests(unittest.TestCase):
    def test_creates_issue_when_source_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            metadata_dir = root / "build-metadata"
            metadata_dir.mkdir()
            failures_file = metadata_dir / "tool-download-failures.json"
            failures_file.write_text(
                json.dumps({
                    "broken-source": {
                        "source": "broken-source",
                        "reasons": ["403 Forbidden: Repository access blocked"],
                    }
                }),
                encoding="utf-8",
            )
            status_file = metadata_dir / "tool-download-status.json"
            status_file.write_text(
                json.dumps({"succeeded": {}, "failed": {"broken-source": {}}}),
                encoding="utf-8",
            )
            patch_config = root / "my-patch-config.json"
            patch_config.write_text(
                json.dumps({
                    "patch_list": [
                        {"app_name": "example-app", "source": "broken-source", "enabled": True}
                    ]
                }),
                encoding="utf-8",
            )
            sources_dir = root / "sources"
            sources_dir.mkdir()
            (sources_dir / "broken-source.json").write_text(
                json.dumps([
                    {"name": "broken-source"},
                    {"user": "MorpheApp", "repo": "morphe-cli", "tag": "latest"},
                    {"user": "broken-user", "repo": "broken-patches", "tag": "latest"},
                ]),
                encoding="utf-8",
            )

            mock_subprocess = mock.Mock()
            mock_subprocess.return_value.stdout = "[]"

            with (
                mock.patch.object(report_tool_download_failures, "FAILURES_FILE", failures_file),
                mock.patch.object(report_tool_download_failures, "STATUS_FILE", status_file),
                mock.patch.object(report_tool_download_failures, "PATCH_CONFIG_FILE", patch_config),
                mock.patch.object(report_tool_download_failures, "SOURCES_DIR", sources_dir),
                mock.patch.dict(os.environ, {"GITHUB_TOKEN": "mock-token"}),
                mock.patch("subprocess.run", mock_subprocess),
            ):
                result = report_tool_download_failures.main()

            self.assertEqual(result, 0)
            create_calls = [
                call for call in mock_subprocess.call_args_list
                if "create" in call[0][0]
            ]
            self.assertEqual(len(create_calls), 1)
            cmd = create_calls[0][0][0]
            self.assertIn("[Source Unavailable] Build tools failed to download for broken-source", cmd)

    def test_reopens_closed_issue_if_source_still_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            metadata_dir = root / "build-metadata"
            metadata_dir.mkdir()
            failures_file = metadata_dir / "tool-download-failures.json"
            failures_file.write_text(
                json.dumps({
                    "broken-source": {
                        "source": "broken-source",
                        "reasons": ["403 Forbidden: Repository access blocked"],
                    }
                }),
                encoding="utf-8",
            )
            status_file = metadata_dir / "tool-download-status.json"
            status_file.write_text(
                json.dumps({"succeeded": {}, "failed": {"broken-source": {}}}),
                encoding="utf-8",
            )
            sources_dir = root / "sources"
            sources_dir.mkdir()
            (sources_dir / "broken-source.json").write_text(
                json.dumps([{"name": "broken-source"}]), encoding="utf-8"
            )

            existing_issues = json.dumps([
                {
                    "number": 1545,
                    "title": "[Source Unavailable] Build tools failed to download for broken-source",
                    "state": "CLOSED",
                }
            ])

            mock_subprocess = mock.Mock()
            mock_subprocess.return_value.stdout = existing_issues

            with (
                mock.patch.object(report_tool_download_failures, "FAILURES_FILE", failures_file),
                mock.patch.object(report_tool_download_failures, "STATUS_FILE", status_file),
                mock.patch.object(report_tool_download_failures, "SOURCES_DIR", sources_dir),
                mock.patch.dict(os.environ, {"GITHUB_TOKEN": "mock-token"}),
                mock.patch("subprocess.run", mock_subprocess),
            ):
                result = report_tool_download_failures.main()

            self.assertEqual(result, 0)
            reopen_calls = [
                call for call in mock_subprocess.call_args_list
                if "reopen" in call[0][0]
            ]
            self.assertEqual(len(reopen_calls), 1)

    def test_does_not_close_issue_for_excluded_unrecovered_source(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            metadata_dir = root / "build-metadata"
            metadata_dir.mkdir()
            failures_file = metadata_dir / "tool-download-failures.json"
            failures_file.write_text("{}", encoding="utf-8")
            status_file = metadata_dir / "tool-download-status.json"
            # succeeded does NOT contain still-excluded source
            status_file.write_text(
                json.dumps({"succeeded": {"other-source": "v1"}, "failed": {}}),
                encoding="utf-8",
            )
            sources_dir = root / "sources"
            sources_dir.mkdir()
            (sources_dir / "still-excluded.json").write_text(
                json.dumps([{"name": "still-excluded"}]), encoding="utf-8"
            )

            existing_issues = json.dumps([
                {
                    "number": 999,
                    "title": "[Source Unavailable] Build tools failed to download for still-excluded",
                    "state": "OPEN",
                }
            ])

            mock_subprocess = mock.Mock()
            mock_subprocess.return_value.stdout = existing_issues

            with (
                mock.patch.object(report_tool_download_failures, "FAILURES_FILE", failures_file),
                mock.patch.object(report_tool_download_failures, "STATUS_FILE", status_file),
                mock.patch.object(report_tool_download_failures, "SOURCES_DIR", sources_dir),
                mock.patch.dict(os.environ, {"GITHUB_TOKEN": "mock-token"}),
                mock.patch("subprocess.run", mock_subprocess),
            ):
                result = report_tool_download_failures.main()

            self.assertEqual(result, 0)
            close_calls = [
                call for call in mock_subprocess.call_args_list
                if "close" in call[0][0]
            ]
            # Should NOT close
            self.assertEqual(len(close_calls), 0)

    def test_closes_recovered_issue_when_source_succeeds(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            metadata_dir = root / "build-metadata"
            metadata_dir.mkdir()
            failures_file = metadata_dir / "tool-download-failures.json"
            failures_file.write_text("{}", encoding="utf-8")
            status_file = metadata_dir / "tool-download-status.json"
            status_file.write_text(
                json.dumps({"succeeded": {"recovered-source": "v1.0.0"}, "failed": {}}),
                encoding="utf-8",
            )
            sources_dir = root / "sources"
            sources_dir.mkdir()
            (sources_dir / "recovered-source.json").write_text(
                json.dumps([{"name": "recovered-source"}]), encoding="utf-8"
            )

            existing_issues = json.dumps([
                {
                    "number": 123,
                    "title": "[Source Unavailable] Build tools failed to download for recovered-source",
                    "state": "OPEN",
                }
            ])

            mock_subprocess = mock.Mock()
            mock_subprocess.return_value.stdout = existing_issues

            with (
                mock.patch.object(report_tool_download_failures, "FAILURES_FILE", failures_file),
                mock.patch.object(report_tool_download_failures, "STATUS_FILE", status_file),
                mock.patch.object(report_tool_download_failures, "SOURCES_DIR", sources_dir),
                mock.patch.dict(os.environ, {"GITHUB_TOKEN": "mock-token"}),
                mock.patch("subprocess.run", mock_subprocess),
            ):
                result = report_tool_download_failures.main()

            self.assertEqual(result, 0)
            close_calls = [
                call for call in mock_subprocess.call_args_list
                if "close" in call[0][0]
            ]
            self.assertEqual(len(close_calls), 1)
            cmd = close_calls[0][0][0]
            self.assertIn("123", cmd)


if __name__ == "__main__":
    unittest.main()
