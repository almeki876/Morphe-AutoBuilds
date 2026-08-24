from __future__ import annotations

import hashlib
import os
import sys
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
    def test_short_non_transfer_command_still_has_a_bounded_timeout(self) -> None:
        with mock.patch.dict(
            os.environ,
            {"GPLAY_VERSION_TIMEOUT_SECONDS": "3"},
            clear=False,
        ):
            timeout = aurora_play._command_timeout(["playfetch", "version"])
        self.assertEqual(timeout, 3.0)

    def test_active_transfer_can_run_longer_than_idle_window(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script = (
                "import pathlib,time; "
                f"p=pathlib.Path({str(root / 'payload.apk')!r}); "
                "h=p.open('wb'); "
                "[(h.write(b'x'*1024),h.flush(),time.sleep(0.35)) for _ in range(5)]; "
                "h.close()"
            )
            with mock.patch.dict(
                os.environ,
                {
                    "GPLAY_TRANSFER_START_TIMEOUT_SECONDS": "1",
                    "GPLAY_TRANSFER_IDLE_TIMEOUT_SECONDS": "1",
                },
                clear=False,
            ):
                started = time.monotonic()
                result = aurora_play._run(
                    [sys.executable, "-c", script],
                    progress_dir=root,
                )
                elapsed = time.monotonic() - started

            self.assertEqual(result.returncode, 0)
            self.assertGreater(elapsed, 1.0)
            self.assertEqual((root / "payload.apk").stat().st_size, 5 * 1024)

    def test_transfer_times_out_when_no_payload_ever_starts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script = (
                "import time; "
                "[(print('still requesting', flush=True), time.sleep(0.25)) for _ in range(12)]"
            )
            with mock.patch.dict(
                os.environ,
                {
                    "GPLAY_TRANSFER_START_TIMEOUT_SECONDS": "1",
                    "GPLAY_TRANSFER_IDLE_TIMEOUT_SECONDS": "1",
                },
                clear=False,
            ):
                with self.assertRaisesRegex(
                    aurora_play.GooglePlayTimeout,
                    "produced no download payload for 1s",
                ):
                    aurora_play._run(
                        [sys.executable, "-c", script],
                        progress_dir=root,
                    )

    def test_started_transfer_times_out_only_after_payload_stalls(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script = (
                "import pathlib,time; "
                f"p=pathlib.Path({str(root / 'payload.apk')!r}); "
                "p.write_bytes(b'partial'); time.sleep(3)"
            )
            with mock.patch.dict(
                os.environ,
                {
                    "GPLAY_TRANSFER_START_TIMEOUT_SECONDS": "1",
                    "GPLAY_TRANSFER_IDLE_TIMEOUT_SECONDS": "1",
                },
                clear=False,
            ):
                with self.assertRaisesRegex(
                    aurora_play.GooglePlayTimeout,
                    "download stalled with no payload progress for 1s",
                ):
                    aurora_play._run(
                        [sys.executable, "-c", script],
                        progress_dir=root,
                    )


if __name__ == "__main__":
    unittest.main()
