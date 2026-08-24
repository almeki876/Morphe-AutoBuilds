from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from scripts.scan_virustotal import _load_cache, _save_cache, _scan_all
from scripts.virustotal import HashLookup, ScanResult, sha256_file


def _result(path: Path, sha256: str) -> ScanResult:
    return ScanResult(
        file=path.name,
        sha256=sha256,
        size=path.stat().st_size,
        analysis_id="analysis-id",
        malicious=0,
        suspicious=0,
        harmless=10,
        undetected=50,
        timeout=0,
        failure=0,
        unsupported=0,
        verdict="clean",
        method="hash lookup",
        engines={},
        permalink=f"https://www.virustotal.com/gui/file/{sha256}",
        last_analysis_date=1,
        reanalyzed=False,
    )


class ExplodingClient:
    def lookup_hash(self, *_args, **_kwargs):
        raise AssertionError("VirusTotal must not be called for a cached SHA-256")

    def prepare_lookup(self, *_args, **_kwargs):
        raise AssertionError("VirusTotal must not be called for a cached SHA-256")

    def finish_lookup(self, *_args, **_kwargs):
        raise AssertionError("VirusTotal must not be called for a cached SHA-256")


class PipelineClient:
    def __init__(self, expected_starts: int):
        self.expected_starts = expected_starts
        self.started: list[str] = []
        self.finished: list[str] = []

    def lookup_hash(self, path: Path, digest: str) -> HashLookup:
        return HashLookup(digest, None, {}, {}, "uploaded", None, False)

    def prepare_lookup(self, path: Path, lookup: HashLookup) -> HashLookup:
        self.started.append(path.name)
        return replace(lookup, analysis_id=f"analysis-{path.name}")

    def finish_lookup(self, path: Path, lookup: HashLookup) -> ScanResult:
        if len(self.started) != self.expected_starts:
            raise AssertionError("polling started before every hash was submitted")
        self.finished.append(path.name)
        return _result(path, lookup.sha256)


class VirusTotalPersistentCacheTests(unittest.TestCase):
    def test_persistent_cache_skips_virustotal_and_deduplicates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "first.apk"
            second = root / "second.apk"
            first.write_bytes(b"same-apk-bytes")
            second.write_bytes(b"same-apk-bytes")
            digest = sha256_file(first)

            cache_path = root / "cache" / "hash-results.json"
            _save_cache(cache_path, {digest: _result(first, digest)})
            cache = _load_cache(cache_path)

            results, failures = _scan_all(
                ExplodingClient(),
                [first, second],
                cache,
                cache_path,
            )

            self.assertEqual(failures, [])
            self.assertEqual(
                [result.file for result in results],
                ["first.apk", "second.apk"],
            )
            self.assertTrue(all(result.sha256 == digest for result in results))
            self.assertTrue(
                all(result.method == "persistent hash cache" for result in results)
            )

    def test_cache_keeps_only_detection_engine_details(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            apk = root / "sample.apk"
            apk.write_bytes(b"sample")
            digest = sha256_file(apk)
            result = replace(
                _result(apk, digest),
                malicious=1,
                verdict="unsafe",
                engines={
                    "CleanEngine": {
                        "category": "undetected",
                        "result": None,
                        "engine_version": "1",
                    },
                    "DetectedEngine": {
                        "category": "malicious",
                        "result": "Example.Test",
                        "engine_version": "2",
                    },
                },
            )
            cache_path = root / "hash-results.json"

            _save_cache(cache_path, {digest: result})

            raw_text = cache_path.read_text(encoding="utf-8")
            payload = json.loads(raw_text)
            engines = payload["results"][digest]["engines"]
            self.assertNotIn("CleanEngine", engines)
            self.assertEqual(engines["DetectedEngine"]["category"], "malicious")
            self.assertNotIn("\n  ", raw_text)

            loaded = _load_cache(cache_path)[digest]
            self.assertEqual(set(loaded.engines), {"DetectedEngine"})
            self.assertEqual(loaded.verdict, "unsafe")

    def test_all_unknown_hashes_are_started_before_any_polling(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "first.apk"
            second = root / "second.apk"
            first.write_bytes(b"first-unique-apk")
            second.write_bytes(b"second-unique-apk")
            cache_path = root / "cache" / "hash-results.json"
            client = PipelineClient(expected_starts=2)

            with patch.dict("os.environ", {"VT_WORKERS": "2"}):
                results, failures = _scan_all(
                    client,
                    [first, second],
                    {},
                    cache_path,
                )

            self.assertEqual(failures, [])
            self.assertCountEqual(client.started, ["first.apk", "second.apk"])
            self.assertCountEqual(client.finished, ["first.apk", "second.apk"])
            self.assertEqual(len(results), 2)
            self.assertEqual(len(_load_cache(cache_path)), 2)


if __name__ == "__main__":
    unittest.main()
