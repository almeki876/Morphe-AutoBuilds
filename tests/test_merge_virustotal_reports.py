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

    def test_same_hash_under_different_file_names_both_survive(self) -> None:
        """Regression: one base APK shared by two patch sources.

        Run 45110765179 lost the youtube-music/morphe scan result because
        the merge keyed results by SHA-256 alone: the morphe and
        revanced-anddea jobs scanned the identical base APK under
        different file names, and only the later chunk's name survived, so
        the release gate found no result for the morphe pair and excluded
        YouTube Music as "unknown".
        """
        shared_sha = (
            "a003ac2e673c2979b2268e5acfbfd74f88b93c4b75af5c7d604b3c7fb92768a4"
        )

        def chunk(file_name: str) -> dict:
            return {
                "results": [
                    {
                        "file": file_name,
                        "sha256": shared_sha,
                        "size": 74431248,
                        "analysis_id": "",
                        "malicious": 0,
                        "suspicious": 0,
                        "harmless": 0,
                        "undetected": 65,
                        "timeout": 0,
                        "failure": 0,
                        "unsupported": 0,
                        "verdict": "clean",
                        "method": "persistent hash cache",
                        "engines": {},
                        "permalink": (
                            "https://www.virustotal.com/gui/file/"
                            f"{shared_sha}/detection"
                        ),
                        "last_analysis_date": 1787164347,
                        "reanalyzed": False,
                    }
                ],
                "failures": [],
                "cache": {
                    "version": 1,
                    "results": {shared_sha: {"sha256": shared_sha}},
                },
            }

        chunks = {
            "vt-report-youtube-music-morphe": "youtube-music-morphe-arm64-v8a.apk",
            "vt-report-youtube-music-revanced-anddea": (
                "youtube-music-revanced-anddea-arm64-v8a.apk"
            ),
        }
        for directory, file_name in chunks.items():
            sub = self.root / directory
            sub.mkdir()
            (sub / "virustotal_base_results.json").write_text(
                json.dumps(chunk(file_name)), encoding="utf-8"
            )

        results, failures, cache = load_chunk_results(self.root)
        self.assertEqual(failures, [])
        self.assertEqual(
            sorted(item["file"] for item in results),
            [
                "youtube-music-morphe-arm64-v8a.apk",
                "youtube-music-revanced-anddea-arm64-v8a.apk",
            ],
        )
        self.assertIn(shared_sha, cache)

        # The release gate matches by "{app}-{source}-" file prefix, so
        # both pairs must resolve as publishable from the merged payload.
        from scripts.virustotal_gate import compute_exclusions

        exclusions = compute_exclusions(
            {"results": results, "failures": failures},
            [("youtube-music", "morphe"), ("youtube-music", "revanced-anddea")],
        )
        self.assertEqual(exclusions, [])


if __name__ == "__main__":
    unittest.main()
