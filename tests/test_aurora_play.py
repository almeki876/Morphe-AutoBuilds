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

    @mock.patch("src.aurora_play._download_with_repo_local_gplaydl")
    @mock.patch("src.aurora_play._download_with_linked_gplaydl")
    def test_linked_account_failure_falls_back_to_repo_local(
        self,
        linked: mock.Mock,
        repo_local: mock.Mock,
    ) -> None:
        linked.side_effect = RuntimeError("linked service unavailable")
        repo_local.return_value = Path("fallback.apk")
        candidate = VersionCandidate(name="1.2.3", code="123")

        with mock.patch.dict(os.environ, {"GPLAYDL_API_KEY": "secret-key"}, clear=False):
            result = aurora_play.download_candidate("com.example.app", candidate, Path("."))

        self.assertEqual(result, Path("fallback.apk"))
        linked.assert_called_once()
        repo_local.assert_called_once()

    @mock.patch("src.aurora_play._ensure_downloader", return_value=Path("helper.jar"))
    @mock.patch("src.aurora_play._run")
    def test_repo_local_fallback_does_not_receive_official_api_key(
        self,
        run: mock.Mock,
        _ensure_downloader: mock.Mock,
    ) -> None:
        def fake_run(command, *, cwd=None, env=None):
            self.assertIsNotNone(env)
            self.assertNotIn("GPLAYDL_API_KEY", env)
            output = Path(command[command.index("--output") + 1])
            package_dir = output / "com.example.app"
            package_dir.mkdir(parents=True)
            (package_dir / "base.apk").write_bytes(b"base")
            return mock.Mock(returncode=0, stdout="ok")

        run.side_effect = fake_run
        with mock.patch.dict(os.environ, {"GPLAYDL_API_KEY": "secret-key"}, clear=False):
            with tempfile.TemporaryDirectory() as directory:
                result = aurora_play._download_with_repo_local_gplaydl(
                    "com.example.app", None, Path(directory)
                )
                self.assertEqual(result.read_bytes(), b"base")

    @mock.patch("src.aurora_play._ensure_downloader", return_value=Path("helper.jar"))
    @mock.patch("src.aurora_play._run")
    def test_download_candidate_passes_exact_version_code_and_keeps_splits(
        self,
        run: mock.Mock,
        _ensure_downloader: mock.Mock,
    ) -> None:
        def fake_run(command, *, cwd=None, env=None):
            output = Path(command[command.index("--output") + 1])
            package_dir = output / "com.amazon.mShop.android.shopping"
            package_dir.mkdir(parents=True)
            (package_dir / "base.apk").write_bytes(b"base")
            (package_dir / "split_config.arm64_v8a.apk").write_bytes(b"split")
            return mock.Mock(returncode=0, stdout="ok")

        run.side_effect = fake_run
        candidate = VersionCandidate(name="32.13.2.100", code="1241320216")
        with mock.patch.dict(os.environ, {"GPLAYDL_API_KEY": ""}, clear=False):
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
        self.assertEqual(command[command.index("--version-code") + 1], "1241320216")
        self.assertIn("--output", command)

    @mock.patch("src.aurora_play._ensure_downloader", return_value=Path("helper.jar"))
    @mock.patch("src.aurora_play._run")
    def test_current_play_download_does_not_guess_version_code(
        self,
        run: mock.Mock,
        _ensure_downloader: mock.Mock,
    ) -> None:
        def fake_run(command, *, cwd=None, env=None):
            output = Path(command[command.index("--output") + 1])
            package_dir = output / "com.example.app"
            package_dir.mkdir(parents=True)
            (package_dir / "base.apk").write_bytes(b"base")
            return mock.Mock(returncode=0, stdout="ok")

        run.side_effect = fake_run
        with mock.patch.dict(os.environ, {"GPLAYDL_API_KEY": ""}, clear=False):
            with tempfile.TemporaryDirectory() as directory:
                result = aurora_play.download_current("com.example.app", Path(directory))
                self.assertEqual(result.read_bytes(), b"base")

        command = run.call_args.args[0]
        self.assertNotIn("--version-code", command)
        self.assertEqual(command[command.index("download") + 1], "com.example.app")

    @mock.patch("src.aurora_play._ensure_downloader")
    def test_adguard_never_touches_google_play(self, ensure_downloader: mock.Mock) -> None:
        with self.assertRaises(aurora_play.GooglePlayDisabled):
            aurora_play.download_current("com.adguard.android", Path("."))
        ensure_downloader.assert_not_called()


if __name__ == "__main__":
    unittest.main()
