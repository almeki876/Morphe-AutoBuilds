import hashlib
import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from scripts import download_all_tools


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class ChecksumParsingTests(unittest.TestCase):
    def test_parse_standard_lines(self) -> None:
        text = (
            f"{_sha256(b'a')}  patches.mpp\n"
            f"{_sha256(b'b')} *cli.jar\n"
            "not-a-digest  ignored.txt\n"
            "\n"
        )
        pairs = dict(
            (filename, digest)
            for filename, digest in download_all_tools._parse_checksum_lines(text)
        )
        self.assertEqual(pairs["patches.mpp"], _sha256(b"a"))
        self.assertEqual(pairs["cli.jar"], _sha256(b"b"))
        self.assertNotIn("ignored.txt", pairs)

    def test_parse_bare_digest(self) -> None:
        pairs = download_all_tools._parse_checksum_lines(_sha256(b"x") + "\n")
        self.assertEqual(pairs, [("", _sha256(b"x"))])


class ExpectedDigestsTests(unittest.TestCase):
    def _download(self, contents: dict[str, bytes]):
        def fake_download(url: str, dest: Path, **kwargs) -> bool:
            if url not in contents:
                return False
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(contents[url])
            return True

        return fake_download

    def test_sidecar_checksum_is_resolved(self) -> None:
        bundle = b"bundle-bytes"
        assets = [
            {"name": "patches.mpp", "browser_download_url": "https://x/patches.mpp"},
            {
                "name": "patches.mpp.sha256",
                "browser_download_url": "https://x/patches.mpp.sha256",
            },
        ]
        contents = {
            "https://x/patches.mpp.sha256": f"{_sha256(bundle)}  patches.mpp\n".encode(),
        }
        with tempfile.TemporaryDirectory() as directory:
            with mock.patch.object(
                download_all_tools, "download_asset", self._download(contents)
            ):
                expected = download_all_tools.expected_asset_digests(
                    assets, ["patches.mpp"], Path(directory)
                )
        self.assertEqual(expected, {"patches.mpp": ("sha256", _sha256(bundle))})

    def test_aggregate_checksum_file_is_resolved(self) -> None:
        assets = [
            {"name": "cli.jar", "browser_download_url": "https://x/cli.jar"},
            {"name": "checksums.txt", "browser_download_url": "https://x/checksums.txt"},
        ]
        contents = {
            "https://x/checksums.txt": f"{_sha256(b'cli')}  cli.jar\n".encode(),
        }
        with tempfile.TemporaryDirectory() as directory:
            with mock.patch.object(
                download_all_tools, "download_asset", self._download(contents)
            ):
                expected = download_all_tools.expected_asset_digests(
                    assets, ["cli.jar"], Path(directory)
                )
        self.assertEqual(expected, {"cli.jar": ("sha256", _sha256(b"cli"))})

    def test_api_digest_field_is_resolved(self) -> None:
        assets = [
            {
                "name": "patches.mpp",
                "browser_download_url": "https://x/patches.mpp",
                "digest": f"sha256:{_sha256(b'bundle')}",
            }
        ]
        with tempfile.TemporaryDirectory() as directory:
            expected = download_all_tools.expected_asset_digests(
                assets, ["patches.mpp"], Path(directory)
            )
        self.assertEqual(expected, {"patches.mpp": ("sha256", _sha256(b"bundle"))})

    def test_api_digest_wins_when_aggregate_disagrees(self) -> None:
        """Regression: rushiranpise SHA256SUMS.txt (issue #1554).

        The aggregate file lists the hash of a local build-output path
        (``patches/build/libs/patches-1.23.1.mpp``), which does not match
        the uploaded asset.  GitHub's API digest is computed from the
        stored asset bytes, so when the two disagree the API digest must
        win; trusting the stale sums file excluded the whole source in
        run 45110765179 even though the asset itself was intact.
        """
        real = _sha256(b"real-bundle-bytes")
        stale = _sha256(b"locally-built-bundle-bytes")
        assets = [
            {
                "name": "patches-1.23.1.mpp",
                "browser_download_url": "https://x/patches-1.23.1.mpp",
                "digest": f"sha256:{real}",
            },
            {
                "name": "SHA256SUMS.txt",
                "browser_download_url": "https://x/SHA256SUMS.txt",
            },
        ]
        contents = {
            "https://x/SHA256SUMS.txt": (
                f"{stale}  patches/build/libs/patches-1.23.1.mpp\n".encode()
            ),
        }
        with tempfile.TemporaryDirectory() as directory:
            with mock.patch.object(
                download_all_tools, "download_asset", self._download(contents)
            ):
                expected = download_all_tools.expected_asset_digests(
                    assets, ["patches-1.23.1.mpp"], Path(directory)
                )
        self.assertEqual(expected, {"patches-1.23.1.mpp": ("sha256", real)})

    def test_aggregate_used_when_api_digest_agrees(self) -> None:
        digest = _sha256(b"bundle-bytes")
        assets = [
            {
                "name": "patches.mpp",
                "browser_download_url": "https://x/patches.mpp",
                "digest": f"sha256:{digest}",
            },
            {
                "name": "SHA256SUMS.txt",
                "browser_download_url": "https://x/SHA256SUMS.txt",
            },
        ]
        contents = {
            "https://x/SHA256SUMS.txt": f"{digest}  patches.mpp\n".encode(),
        }
        with tempfile.TemporaryDirectory() as directory:
            with mock.patch.object(
                download_all_tools, "download_asset", self._download(contents)
            ):
                expected = download_all_tools.expected_asset_digests(
                    assets, ["patches.mpp"], Path(directory)
                )
        self.assertEqual(expected, {"patches.mpp": ("sha256", digest)})

    def test_no_checksum_anywhere_means_unverified(self) -> None:
        assets = [{"name": "patches.mpp", "browser_download_url": "https://x/p"}]
        with tempfile.TemporaryDirectory() as directory:
            expected = download_all_tools.expected_asset_digests(
                assets, ["patches.mpp"], Path(directory)
            )
        self.assertEqual(expected, {})

    def test_verify_asset_checksum(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "file.bin"
            path.write_bytes(b"payload")
            self.assertTrue(
                download_all_tools.verify_asset_checksum(
                    path, ("sha256", _sha256(b"payload"))
                )
            )
            self.assertFalse(
                download_all_tools.verify_asset_checksum(
                    path, ("sha256", _sha256(b"other"))
                )
            )


class MainChecksumGateTests(unittest.TestCase):
    def _run_main(self, root: Path, bundle_digest: str) -> int:
        sources_dir = root / "sources"
        sources_dir.mkdir(parents=True)
        (sources_dir / "testsrc.json").write_text(
            json.dumps(
                [
                    {"name": "testsrc"},
                    {"user": "MorpheApp", "repo": "morphe-cli", "tag": "latest"},
                    {"user": "someone", "repo": "someone-patches", "tag": "latest"},
                ]
            ),
            encoding="utf-8",
        )
        contents = {
            "https://example.invalid/cli.jar": b"cli-bytes",
            "https://example.invalid/patches.mpp": b"bundle-bytes",
            "https://example.invalid/patches.mpp.sha256": (
                f"{bundle_digest}  patches.mpp\n".encode()
            ),
        }

        def fake_download(url: str, dest: Path, **kwargs) -> bool:
            if url not in contents:
                return False
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(contents[url])
            return True

        def fake_detect(user: str, repo: str, tag: str, **kwargs):
            if repo == "morphe-cli":
                return {
                    "tag_name": "v1.0.0",
                    "assets": [
                        {
                            "name": "cli.jar",
                            "browser_download_url": "https://example.invalid/cli.jar",
                        }
                    ],
                }
            return {
                "tag_name": "v2.0.0",
                "assets": [
                    {
                        "name": "patches.mpp",
                        "browser_download_url": "https://example.invalid/patches.mpp",
                    },
                    {
                        "name": "patches.mpp.sha256",
                        "browser_download_url": "https://example.invalid/patches.mpp.sha256",
                    },
                ],
            }

        fake_src = types.ModuleType("src")
        fake_src.utils = types.ModuleType("src.utils")
        fake_src.utils.detect_github_release = fake_detect

        original_cwd = Path.cwd()
        try:
            os.chdir(root)
            with (
                mock.patch.dict(
                    sys.modules, {"src": fake_src, "src.utils": fake_src.utils}
                ),
                mock.patch.dict(
                    os.environ,
                    {"SOURCE_TAGS_JSON": json.dumps({"testsrc": "v2.0.0"})},
                    clear=False,
                ),
                mock.patch.object(download_all_tools, "SOURCES_DIR", sources_dir),
                mock.patch.object(
                    download_all_tools, "TOOLS_DIR", root / "tools"
                ),
                mock.patch.object(download_all_tools, "download_asset", fake_download),
            ):
                return download_all_tools.main()
        finally:
            os.chdir(original_cwd)

    def test_matching_checksum_keeps_the_source(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = self._run_main(root, _sha256(b"bundle-bytes"))
            self.assertEqual(result, 0)
            self.assertTrue((root / "tools" / "testsrc" / "patches.mpp").is_file())
            self.assertTrue((root / "tools" / "testsrc" / "cli.jar").is_file())

    def test_mismatched_checksum_excludes_the_source(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = self._run_main(root, _sha256(b"tampered-bytes"))
            self.assertEqual(result, 1)
            self.assertFalse((root / "tools" / "testsrc").exists())
            status = json.loads(
                (root / "build-metadata" / "tool-download-status.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertIn("testsrc", status["failed"])
            reasons = status["failed"]["testsrc"]["reasons"]
            self.assertTrue(any("checksum mismatch" in r for r in reasons))

    def test_transient_corruption_is_retried_once(self) -> None:
        """A first download that arrives corrupted is fetched once more.

        Only a repeated mismatch may exclude the source; a single bad
        transfer must not (it costs a whole patch source otherwise).
        """
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sources_dir = root / "sources"
            sources_dir.mkdir(parents=True)
            (sources_dir / "testsrc.json").write_text(
                json.dumps(
                    [
                        {"name": "testsrc"},
                        {"user": "MorpheApp", "repo": "morphe-cli", "tag": "latest"},
                        {"user": "someone", "repo": "someone-patches", "tag": "latest"},
                    ]
                ),
                encoding="utf-8",
            )
            contents = {
                "https://example.invalid/cli.jar": b"cli-bytes",
                "https://example.invalid/patches.mpp": b"bundle-bytes",
                "https://example.invalid/patches.mpp.sha256": (
                    f"{_sha256(b'bundle-bytes')}  patches.mpp\n".encode()
                ),
            }
            attempts = {"patches": 0}

            def flaky_download(url: str, dest: Path, **kwargs) -> bool:
                if url not in contents:
                    return False
                dest.parent.mkdir(parents=True, exist_ok=True)
                if url.endswith("patches.mpp"):
                    attempts["patches"] += 1
                    if attempts["patches"] == 1:
                        dest.write_bytes(b"corrupted-in-transit")
                        return True
                dest.write_bytes(contents[url])
                return True

            def fake_detect(user: str, repo: str, tag: str, **kwargs):
                if repo == "morphe-cli":
                    return {
                        "tag_name": "v1.0.0",
                        "assets": [
                            {
                                "name": "cli.jar",
                                "browser_download_url": "https://example.invalid/cli.jar",
                            }
                        ],
                    }
                return {
                    "tag_name": "v2.0.0",
                    "assets": [
                        {
                            "name": "patches.mpp",
                            "browser_download_url": "https://example.invalid/patches.mpp",
                        },
                        {
                            "name": "patches.mpp.sha256",
                            "browser_download_url": "https://example.invalid/patches.mpp.sha256",
                        },
                    ],
                }

            fake_src = types.ModuleType("src")
            fake_src.utils = types.ModuleType("src.utils")
            fake_src.utils.detect_github_release = fake_detect

            original_cwd = Path.cwd()
            try:
                os.chdir(root)
                with (
                    mock.patch.dict(
                        sys.modules, {"src": fake_src, "src.utils": fake_src.utils}
                    ),
                    mock.patch.dict(
                        os.environ,
                        {"SOURCE_TAGS_JSON": json.dumps({"testsrc": "v2.0.0"})},
                        clear=False,
                    ),
                    mock.patch.object(
                        download_all_tools, "SOURCES_DIR", sources_dir
                    ),
                    mock.patch.object(
                        download_all_tools, "TOOLS_DIR", root / "tools"
                    ),
                    mock.patch.object(
                        download_all_tools, "download_asset", flaky_download
                    ),
                ):
                    result = download_all_tools.main()
            finally:
                os.chdir(original_cwd)

            self.assertEqual(result, 0)
            self.assertEqual(attempts["patches"], 2)
            self.assertEqual(
                (root / "tools" / "testsrc" / "patches.mpp").read_bytes(),
                b"bundle-bytes",
            )


if __name__ == "__main__":
    unittest.main()
