from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import create_release, install_google_play_clients, resolve_build_tools, resolve_download_result


class ResolveBuildToolsTests(unittest.TestCase):
    def test_supplied_source_tags_skip_resolution(self) -> None:
        with mock.patch.object(resolve_build_tools, "iter_sources", return_value=[{"name": "new"}]), mock.patch.object(
            resolve_build_tools, "resolve_source_tag"
        ) as resolve:
            result = resolve_build_tools.resolve_all({"SOURCE_TAGS_JSON": '{"new":"v1.2.3"}'})
        self.assertEqual(result, {"new": "v1.2.3"})
        resolve.assert_not_called()

    def test_cache_key_is_independent_of_source_order(self) -> None:
        resolved = {
            "morphe": "m",
            "anddea": "a",
            "rushiranpise": "r",
            "rookie": "k",
            "tosox": "t",
            "yuzu": "y",
            "dropped": "d",
        }
        first = resolve_build_tools.cache_key("hash", resolved)
        self.assertEqual(first, resolve_build_tools.cache_key("hash", dict(reversed(resolved.items()))))
        self.assertRegex(first, r"^build-tools-hash-[0-9a-f]{20}$")


class InstallGooglePlayClientsTests(unittest.TestCase):
    def test_sha256_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample"
            path.write_bytes(b"abc")
            self.assertEqual(
                install_google_play_clients.sha256_file(path),
                "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
            )


class ResolveDownloadResultTests(unittest.TestCase):
    def test_japan_success_is_success(self) -> None:
        success, message = resolve_download_result.outcome_message("failure", "success", "skipped")
        self.assertTrue(success)
        self.assertIn("Japanese Tailscale fallback succeeded", message)

    def test_all_fail_is_failure(self) -> None:
        success, _ = resolve_download_result.outcome_message("failure", "failure", "failure")
        self.assertFalse(success)


class CreateReleaseTests(unittest.TestCase):
    def test_create_release_uploads_apks_and_portable_vt_cache(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            release_dir = root / "release-apks"
            release_dir.mkdir()
            apk = release_dir / "app.apk"
            apk.write_bytes(b"apk")
            report = root / "report.json"
            report.write_text("{}", encoding="utf-8")
            cache = root / "cache.json"

            with mock.patch.object(create_release, "RELEASE_DIR", release_dir), mock.patch.object(
                create_release, "VT_REPORT", report
            ), mock.patch.object(create_release, "VT_CACHE", cache), mock.patch.object(
                create_release,
                "export_cache",
                side_effect=lambda _report, output: output.write_text("{}\n", encoding="utf-8") or 1,
            ) as export_cache, mock.patch.object(create_release, "run") as run:
                create_release.create_release("2026-test", "owner/repo")

            export_cache.assert_called_once_with(report, cache)
            commands = [call.args[0] for call in run.call_args_list]
            self.assertIn(["gh", "release", "upload", "2026-test", str(apk)], commands)
            self.assertIn(
                [
                    "gh",
                    "release",
                    "upload",
                    "2026-test",
                    str(cache),
                    "--repo",
                    "owner/repo",
                    "--clobber",
                ],
                commands,
            )


class WorkflowDelegationTests(unittest.TestCase):
    def test_build_workflow_delegates_large_shell_blocks_to_scripts(self) -> None:
        workflow = Path(".github/workflows/build.yml").read_text(encoding="utf-8")
        self.assertIn("python3 scripts/resolve_build_tools.py", workflow)
        self.assertEqual(workflow.count("python3 scripts/install_google_play_clients.py"), 2)
        self.assertIn("python3 scripts/resolve_download_result.py", workflow)
        self.assertIn("python3 -m scripts.create_release", workflow)
        self.assertNotIn("resolve_tag() {", workflow)
        self.assertNotIn("xargs -0 -r -P 8", workflow)


if __name__ == "__main__":
    unittest.main()
