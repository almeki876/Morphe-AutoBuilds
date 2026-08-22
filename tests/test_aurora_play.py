import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from src import aurora_play
from src.versioning import VersionCandidate


class AuroraPlayTests(unittest.TestCase):
    def test_package_apks_keeps_base_and_splits(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base = root / "base.apk"
            split = root / "split_config.arm64_v8a.apk"
            base.write_bytes(b"base")
            split.write_bytes(b"split")

            result = aurora_play._package_apks(
                [base, split], "com.example.app", root
            )

            self.assertEqual(result.suffix, ".apks")
            with zipfile.ZipFile(result) as archive:
                self.assertEqual(
                    set(archive.namelist()),
                    {"base.apk", "split_config.arm64_v8a.apk"},
                )

    @mock.patch("src.aurora_play.shutil.which", return_value="/usr/bin/gplaydl")
    @mock.patch("src.aurora_play._run")
    def test_linked_gplaydl_uses_api_key_route_and_exact_version_code(
        self,
        run: mock.Mock,
        _which: mock.Mock,
    ) -> None:
        def fake_run(command, *, cwd=None, env=None):
            output = Path(command[command.index("-o") + 1])
            (output / "com.example.app-123-base.apk").write_bytes(b"base")
            (output / "com.example.app-123-config.arm64_v8a.apk").write_bytes(b"split")
            return mock.Mock(returncode=0, stdout="ok")

        run.side_effect = fake_run
        candidate = VersionCandidate(name="1.2.3", code="123")
        with mock.patch.dict(os.environ, {"GPLAYDL_API_KEY": "secret-key"}, clear=False):
            with tempfile.TemporaryDirectory() as directory:
                result = aurora_play.download_candidate(
                    "com.example.app", candidate, Path(directory)
                )
                self.assertEqual(result.suffix, ".apks")

        command = run.call_args.args[0]
        self.assertEqual(command[:3], ["/usr/bin/gplaydl", "download", "com.example.app"])
        self.assertEqual(command[command.index("-v") + 1], "123")
        self.assertEqual(command[command.index("-a") + 1], "arm64")
        self.assertNotIn("secret-key", command)

    @mock.patch("src.aurora_play._download_with_linked_gplaydl")
    def test_linked_account_failure_does_not_fall_back_anonymously(
        self,
        linked: mock.Mock,
    ) -> None:
        linked.side_effect = RuntimeError("linked service unavailable")
        candidate = VersionCandidate(name="1.2.3", code="123")

        with mock.patch.dict(os.environ, {"GPLAYDL_API_KEY": "secret-key"}, clear=False):
            with self.assertRaisesRegex(RuntimeError, "linked service unavailable"):
                aurora_play.download_candidate("com.example.app", candidate, Path("."))

        linked.assert_called_once_with("com.example.app", candidate, Path("."))

    def test_missing_api_key_refuses_google_play(self) -> None:
        candidate = VersionCandidate(name="1.2.3", code="123")
        with mock.patch.dict(os.environ, {"GPLAYDL_API_KEY": ""}, clear=False):
            with self.assertRaisesRegex(RuntimeError, "anonymous Google Play downloads are disabled"):
                aurora_play.download_candidate("com.example.app", candidate, Path("."))

    @mock.patch("src.aurora_play.shutil.which", return_value="/usr/bin/gplaydl")
    @mock.patch("src.aurora_play._run")
    def test_download_candidate_passes_exact_version_code_and_keeps_splits(
        self,
        run: mock.Mock,
        _which: mock.Mock,
    ) -> None:
        def fake_run(command, *, cwd=None, env=None):
            output = Path(command[command.index("-o") + 1])
            (output / "base.apk").write_bytes(b"base")
            (output / "split_config.arm64_v8a.apk").write_bytes(b"split")
            return mock.Mock(returncode=0, stdout="ok")

        run.side_effect = fake_run
        candidate = VersionCandidate(name="32.13.2.100", code="1241320216")
        with mock.patch.dict(os.environ, {"GPLAYDL_API_KEY": "secret-key"}, clear=False):
            with tempfile.TemporaryDirectory() as directory:
                result = aurora_play.download_candidate(
                    "com.amazon.mShop.android.shopping",
                    candidate,
                    Path(directory),
                )
                self.assertTrue(result.is_file())
                with zipfile.ZipFile(result) as archive:
                    self.assertIn("base.apk", archive.namelist())
                    self.assertIn("split_config.arm64_v8a.apk", archive.namelist())

        command = run.call_args.args[0]
        self.assertEqual(command[command.index("download") + 1], "com.amazon.mShop.android.shopping")
        self.assertEqual(command[command.index("-v") + 1], "1241320216")
        self.assertIn("-o", command)

    @mock.patch("src.aurora_play.shutil.which", return_value="/usr/bin/gplaydl")
    @mock.patch("src.aurora_play._run")
    def test_current_play_download_does_not_guess_version_code(
        self,
        run: mock.Mock,
        _which: mock.Mock,
    ) -> None:
        def fake_run(command, *, cwd=None, env=None):
            output = Path(command[command.index("-o") + 1])
            (output / "base.apk").write_bytes(b"base")
            return mock.Mock(returncode=0, stdout="ok")

        run.side_effect = fake_run
        with mock.patch.dict(os.environ, {"GPLAYDL_API_KEY": "secret-key"}, clear=False):
            with tempfile.TemporaryDirectory() as directory:
                result = aurora_play.download_current("com.example.app", Path(directory))
                self.assertEqual(result.read_bytes(), b"base")

        command = run.call_args.args[0]
        self.assertNotIn("-v", command)
        self.assertEqual(command[command.index("download") + 1], "com.example.app")

    @mock.patch("src.aurora_play._download_with_linked_gplaydl")
    def test_adguard_never_touches_google_play(self, linked: mock.Mock) -> None:
        with self.assertRaises(aurora_play.GooglePlayDisabled):
            aurora_play.download_current("com.adguard.android", Path("."))
        linked.assert_not_called()


if __name__ == "__main__":
    unittest.main()
