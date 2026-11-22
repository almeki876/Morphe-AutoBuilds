"""Unit tests for scripts/merge_virustotal_reports.py."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from scripts.merge_virustotal_reports import load_chunk_results, merge_reports


class MergeVirusTotalReportsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_empty_directory_returns_empty(self) -> None:
        results, failures, cache = load_chunk_results(self.root)
        self.assertEqual(results, [])
        self.assertEqual(failures, [])
        self.assertEqual(cache, {})

    def test_merge_multiple_chunks_deduplicates(self) -> None:
        chunk1 = {
            "results": [
                {
                    "file": "app1.apk",
                    "sha256": "aaaa11112222",
                    "permalink": "https://virustotal.com/gui/file/aaaa11112222",
                    "method": "sha256",
                    "last_analysis_date": 1700000000,
                    "reanalyzed": False,
                    "malicious": 0,
                    "suspicious": 0,
                    "undetected": 70,
                    "harmless": 0,
                    "timeout": 0,
                    "confirmed_timeout": 0,
                    "failure": 0,
                    "type_unsupported": 0,
                    "verdict": "clean",
                    "engines": {},
                }
            ],
            "failures": [],
            "cache": {
                "version": 1,
                "results": {
                    "aaaa11112222": {"sha256": "aaaa11112222"}
                }
            }
        }
        chunk2 = {
            "results": [
                {
                    "file": "app2.apk",
                    "sha256": "bbbb33334444",
                    "permalink": "https://virustotal.com/gui/file/bbbb33334444",
                    "method": "sha256",
                    "last_analysis_date": 1700000001,
                    "reanalyzed": False,
                    "malicious": 0,
                    "suspicious": 0,
                    "undetected": 72,
                    "harmless": 0,
                    "timeout": 0,
                    "confirmed_timeout": 0,
                    "failure": 0,
                    "type_unsupported": 0,
                    "verdict": "clean",
                    "engines": {},
                }
            ],
            "failures": ["error downloading foo"],
            "cache": {
                "version": 1,
                "results": {
                    "bbbb33334444": {"sha256": "bbbb33334444"}
                }
            }
        }
        (self.root / "sub1").mkdir()
        (self.root / "sub2").mkdir()
        (self.root / "sub1" / "report.json").write_text(json.dumps(chunk1), encoding="utf-8")
        (self.root / "sub2" / "report.json").write_text(json.dumps(chunk2), encoding="utf-8")

        output_json = self.root / "merged.json"
        output_md = self.root / "merged.md"
        code = merge_reports(self.root, output_json, output_md, "VirusTotal base APK scan results (before patching)")
        self.assertEqual(code, 0)
        self.assertTrue(output_json.is_file())
        self.assertTrue(output_md.is_file())

        data = json.loads(output_json.read_text(encoding="utf-8"))
        self.assertEqual(len(data["results"]), 2)
        self.assertEqual(data["failures"], ["error downloading foo"])
        self.assertIn("aaaa11112222", data["cache"]["results"])
        self.assertIn("bbbb33334444", data["cache"]["results"])

        md_content = output_md.read_text(encoding="utf-8")
        self.assertIn("app1.apk", md_content)
        self.assertIn("app2.apk", md_content)
        self.assertIn("VirusTotal base APK scan results (before patching)", md_content)


if __name__ == "__main__":
    unittest.main()
