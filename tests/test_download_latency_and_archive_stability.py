from __future__ import annotations

import hashlib
import os
import subprocess
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from src import archive_stability, aurora_play


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ArchiveStabilityTests(unittest.TestCase):
    def test_google_play_split_container_is_stable_across_mtime_changes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base = root / "base.apk"
            split = root / "split_config.arm64_v8a.apk"
            base.write_bytes(b"same-base")
            split.write_bytes(b"same-split")

            first = aurora_play._package_apks(
                [base, split], "com.example.first", root
            )
            first_digest = digest(first)

            os.utime(base, (1_900_000_000, 1_900_000_000))
            os.utime(split, (1_800_000_000, 1_800_000_000))
            second = aurora_play._package_apks(
                [split, base], "com.example.second", root
            )

            self.assertEqual(first_digest, digest(second))
            with zipfile.ZipFile(second) as archive:
                self.assertEqual(
                    archive.namelist(),
                    ["base.apk", "split_config.arm64_v8a.apk"],
                )

    def test_canonicalized_scan_archive_ignores_zip_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "first.apks"
            second = root / "second.apks"
            with zipfile.ZipFile(first, "w") as archive:
                info = zipfile.ZipInfo("base.apk", (2020, 1, 2, 3, 4, 6))
                archive.writestr(info, b"payload")
            with zipfile.ZipFile(second, "w") as archive:
                info = zipfile.ZipInfo("base.apk", (2026, 8, 24, 9, 59, 58))
                info.comment = b"different metadata"
                archive.writestr(info, b"payload")

            first_stable = root / "first-stable.apks"
            second_stable = root / "second-stable.apks"
            archive_stability.canonicalize_zip(first, first_stable)
            archive_stability.canonicalize_zip(second, second_stable)

            self.assertEqual(digest(first_stable), digest(second_stable))


class GooglePlayLatencyPolicyTests(unittest.TestCase):
    def test_run_converts_subprocess_timeout_to_google_play_timeout(self) -> None:
        timeout_error = subprocess.TimeoutExpired(["playfetch", "pull"], timeout=2)
        with (
            mock.patch.dict(
                os.environ,
                {"GPLAY_PLAYFETCH_TIMEOUT_SECONDS": "2"},
                clear=False,
            ),
            mock.patch("src.aurora_play.subprocess.run", side_effect=timeout_error) as run,
        ):
            with self.assertRaisesRegex(
                aurora_play.GooglePlayTimeout,
                "playfetch Google Play attempt exceeded 2s",
            ):
                aurora_play._run(["playfetch", "pull", "com.example.app"])

        self.assertEqual(run.call_args.kwargs["timeout"], 2.0)

    def test_shared_play_deadline_caps_each_command(self) -> None:
        now = time.monotonic()
        token = aurora_play._play_deadline.set(now + 3.0)
        try:
            with mock.patch("src.aurora_play.time.monotonic", return_value=now):
                timeout = aurora_play._command_timeout(
                    ["playfetch", "pull", "com.example.app"]
                )
        finally:
            aurora_play._play_deadline.reset(token)

        self.assertEqual(timeout, 3.0)

    def test_exhausted_play_deadline_fails_before_starting_next_client(self) -> None:
        now = time.monotonic()
        token = aurora_play._play_deadline.set(now - 1.0)
        try:
            with mock.patch("src.aurora_play.time.monotonic", return_value=now):
                with self.assertRaisesRegex(
                    aurora_play.GooglePlayTimeout,
                    "preference budget exhausted",
                ):
                    aurora_play._command_timeout(
                        ["apkeep", "-a", "com.example.app"]
                    )
        finally:
            aurora_play._play_deadline.reset(token)


if __name__ == "__main__":
    unittest.main()
