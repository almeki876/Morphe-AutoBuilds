from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.scan_virustotal import _load_cache, _save_cache, _scan_all
from scripts.virustotal import ScanResult, sha256_file


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

    def scan_lookup(self, *_args, **_kwargs):
        raise AssertionError("VirusTotal must not be called for a cached SHA-256")


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


if __name__ == "__main__":
    unittest.main()
