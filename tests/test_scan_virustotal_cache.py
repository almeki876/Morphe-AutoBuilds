from __future__ import annotations

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


def test_persistent_cache_skips_virustotal_and_deduplicates(tmp_path: Path) -> None:
    first = tmp_path / "first.apk"
    second = tmp_path / "second.apk"
    first.write_bytes(b"same-apk-bytes")
    second.write_bytes(b"same-apk-bytes")
    digest = sha256_file(first)

    cache_path = tmp_path / "cache" / "hash-results.json"
    _save_cache(cache_path, {digest: _result(first, digest)})
    cache = _load_cache(cache_path)

    results, failures = _scan_all(
        ExplodingClient(),
        [first, second],
        cache,
        cache_path,
    )

    assert failures == []
    assert [result.file for result in results] == ["first.apk", "second.apk"]
    assert all(result.sha256 == digest for result in results)
    assert all(result.method == "persistent hash cache" for result in results)
