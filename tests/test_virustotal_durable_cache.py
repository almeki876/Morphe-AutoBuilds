from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.scan_virustotal import (
    CACHE_VERSION,
    ScanTelemetry,
    _cache_payload,
    _parse_cache_payload,
    _restore_release_cache,
    _write_reports,
)
from scripts.virustotal import ScanResult


def result(name: str, sha256: str, method: str = "hash lookup") -> ScanResult:
    return ScanResult(
        file=name,
        sha256=sha256,
        size=123,
        analysis_id="",
        malicious=0,
        suspicious=0,
        harmless=10,
        undetected=50,
        timeout=0,
        failure=0,
        unsupported=0,
        verdict="clean",
        method=method,
        engines={},
        permalink=f"https://www.virustotal.com/gui/file/{sha256}/detection",
        last_analysis_date=1,
        reanalyzed=False,
    )


class FakeResponse:
    def __init__(self, payload: dict):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


class VirusTotalDurableCacheTests(unittest.TestCase):
    def test_cache_payload_round_trip(self) -> None:
        digest = "a" * 64
        payload = _cache_payload({digest: result("sample.apk", digest)})
        self.assertEqual(payload["version"], CACHE_VERSION)
        parsed = _parse_cache_payload(payload)
        self.assertEqual(set(parsed), {digest})
        self.assertEqual(parsed[digest].verdict, "clean")

    def test_release_cache_only_fills_missing_entries(self) -> None:
        local_sha = "a" * 64
        remote_sha = "b" * 64
        local = result("local.apk", local_sha, method="local")
        conflicting_remote = result("remote-copy.apk", local_sha, method="remote")
        remote = result("remote.apk", remote_sha, method="remote")
        cache = {local_sha: local}
        payload = _cache_payload({local_sha: conflicting_remote, remote_sha: remote})

        with patch.dict(os.environ, {"GITHUB_REPOSITORY": "owner/repo"}, clear=False):
            with patch("scripts.scan_virustotal.urlopen", return_value=FakeResponse(payload)):
                added = _restore_release_cache(cache)

        self.assertEqual(added, 1)
        self.assertIs(cache[local_sha], local)
        self.assertEqual(cache[remote_sha].method, "remote")

    def test_invalid_release_cache_is_ignored(self) -> None:
        cache = {}
        with patch.dict(os.environ, {"GITHUB_REPOSITORY": "owner/repo"}, clear=False):
            with patch(
                "scripts.scan_virustotal.urlopen",
                return_value=FakeResponse({"version": 999, "results": {}}),
            ):
                self.assertEqual(_restore_release_cache(cache), 0)
        self.assertEqual(cache, {})

    def test_json_report_contains_portable_cache_and_telemetry(self) -> None:
        digest = "c" * 64
        scan_result = result("sample.apk", digest)
        telemetry = ScanTelemetry(
            total_files=1,
            unique_hashes=1,
            cache_hits=1,
            elapsed_seconds=1.25,
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            markdown = root / "report.md"
            report_json = root / "report.json"
            _write_reports(
                markdown,
                report_json,
                [scan_result],
                [],
                "Test",
                cache={digest: scan_result},
                telemetry=telemetry,
                client_metrics={"logical_requests": 0},
            )
            payload = json.loads(report_json.read_text(encoding="utf-8"))

        self.assertEqual(payload["cache"]["version"], CACHE_VERSION)
        self.assertIn(digest, payload["cache"]["results"])
        self.assertEqual(payload["telemetry"]["cache_hits"], 1)
        self.assertEqual(payload["api_metrics"]["logical_requests"], 0)


if __name__ == "__main__":
    unittest.main()
