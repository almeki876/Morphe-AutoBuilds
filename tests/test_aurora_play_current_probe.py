import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src import apk_identity, aurora_play
from src.versioning import VersionCandidate


class AuroraPlayCurrentProbeTests(unittest.TestCase):
    @mock.patch("src.aurora_play.apk_identity.validate_identity")
    @mock.patch("src.aurora_play.shutil.which", return_value="/usr/bin/gplaydl")
    @mock.patch("src.aurora_play._run")
    def test_exact_failure_probes_current_and_accepts_only_exact_identity(
        self,
        run: mock.Mock,
        _which: mock.Mock,
        validate_identity: mock.Mock,
    ) -> None:
        commands: list[list[str]] = []

        def fake_run(command, *, cwd=None, env=None):
            commands.append(list(command))
            if len(commands) == 1:
                return mock.Mock(returncode=1, stdout="requested version unavailable")
            output = Path(command[command.index("-o") + 1])
            (output / "base.apk").write_bytes(b"base")
            return mock.Mock(returncode=0, stdout="downloaded current release")

        run.side_effect = fake_run
        candidate = VersionCandidate(name="14.0.6", code="1400060037")
        validate_identity.return_value = apk_identity.ApkIdentity(
            package_name="jp.ne.ibis.ibispaintx.app",
            version_name="14.0.6",
            version_code="1400060037",
        )

        with mock.patch.dict(os.environ, {"GPLAYDL_API_KEY": "secret-key"}, clear=False):
            with tempfile.TemporaryDirectory() as directory:
                result = aurora_play._download_with_linked_gplaydl(
                    "jp.ne.ibis.ibispaintx.app",
                    candidate,
                    Path(directory),
                )

                self.assertTrue(result.is_file())
                self.assertEqual(result.read_bytes(), b"base")

        self.assertEqual(len(commands), 2)
        self.assertEqual(commands[0][commands[0].index("-v") + 1], "1400060037")
        self.assertNotIn("-v", commands[1])
        validate_identity.assert_called_once_with(
            result,
            "jp.ne.ibis.ibispaintx.app",
            candidate,
        )

    @mock.patch("src.aurora_play.apk_identity.validate_identity")
    @mock.patch("src.aurora_play.shutil.which", return_value="/usr/bin/gplaydl")
    @mock.patch("src.aurora_play._run")
    def test_mismatched_current_probe_is_deleted_and_error_propagates(
        self,
        run: mock.Mock,
        _which: mock.Mock,
        validate_identity: mock.Mock,
    ) -> None:
        commands: list[list[str]] = []

        def fake_run(command, *, cwd=None, env=None):
            commands.append(list(command))
            if len(commands) == 1:
                return mock.Mock(returncode=1, stdout="requested version unavailable")
            output = Path(command[command.index("-o") + 1])
            (output / "base.apk").write_bytes(b"wrong-current")
            return mock.Mock(returncode=0, stdout="downloaded current release")

        run.side_effect = fake_run
        validate_identity.side_effect = apk_identity.ApkIdentityError(
            "APK version mismatch: current release differs"
        )
        candidate = VersionCandidate(name="32.13.2.100", code="1241320216")

        with mock.patch.dict(os.environ, {"GPLAYDL_API_KEY": "secret-key"}, clear=False):
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                with self.assertRaises(apk_identity.ApkIdentityError):
                    aurora_play.download_candidate(
                        "com.amazon.mShop.android.shopping",
                        candidate,
                        root,
                    )
                self.assertFalse(
                    (root / "com.amazon.mShop.android.shopping-google-play.apk").exists()
                )

        self.assertEqual(len(commands), 2)
        self.assertIn("-v", commands[0])
        self.assertNotIn("-v", commands[1])


if __name__ == "__main__":
    unittest.main()
