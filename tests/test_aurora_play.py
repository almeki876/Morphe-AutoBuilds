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

    @mock.patch("src.aurora_play._ensure_downloader", return_value=Path("helper.jar"))
    @mock.patch("src.aurora_play._run")
    def test_download_candidate_passes_exact_version_code_and_keeps_splits(
        self,
        run: mock.Mock,
        _ensure_downloader: mock.Mock,
    ) -> None:
        observed_env = {}

        def fake_run(command, *, cwd=None, env=None):
            observed_env.update(env or {})
            output = Path(command[command.index("-o") + 1])
            package_dir = output / "com.amazon.mShop.android.shopping"
            package_dir.mkdir(parents=True)
            (package_dir / "base.apk").write_bytes(b"base")
            (package_dir / "split_config.arm64_v8a.apk").write_bytes(b"split")
            return mock.Mock(returncode=0, stdout="ok")

        run.side_effect = fake_run
        candidate = VersionCandidate(name="32.13.2.100", code="1241322016")
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

        self.assertEqual(observed_env["GPLAY_VERSION_CODE"], "1241322016")
        command = run.call_args.args[0]
        self.assertIn("-d", command)
        self.assertIn("com.amazon.mShop.android.shopping", Path(command[command.index("-a") + 1]).read_text() if Path(command[command.index("-a") + 1]).exists() else "com.amazon.mShop.android.shopping")

    @mock.patch("src.aurora_play._ensure_downloader", return_value=Path("helper.jar"))
    @mock.patch("src.aurora_play._run")
    def test_current_play_download_does_not_guess_version_code(
        self,
        run: mock.Mock,
        _ensure_downloader: mock.Mock,
    ) -> None:
        seen_env = {}

        def fake_run(command, *, cwd=None, env=None):
            seen_env.update(env or {})
            output = Path(command[command.index("-o") + 1])
            package_dir = output / "com.example.app"
            package_dir.mkdir(parents=True)
            (package_dir / "base.apk").write_bytes(b"base")
            return mock.Mock(returncode=0, stdout="ok")

        run.side_effect = fake_run
        with tempfile.TemporaryDirectory() as directory:
            result = aurora_play.download_current("com.example.app", Path(directory))
            self.assertEqual(result.read_bytes(), b"base")

        self.assertNotIn("GPLAY_VERSION_CODE", seen_env)


if __name__ == "__main__":
    unittest.main()
