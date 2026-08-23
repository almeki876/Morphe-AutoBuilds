import os
import stat
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from src import aurora_play
from src.apk_identity import ApkIdentity
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
    def test_linked_gplaydl_invokes_upstream_cli_with_exact_version_code(
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
        with mock.patch.dict(
            os.environ,
            {
                "GPLAYDL_API_KEY": "secret-key",
                "GPLAYDL_DISPENSER_URL": "",
            },
            clear=False,
        ):
            with tempfile.TemporaryDirectory() as directory:
                result = aurora_play.download_candidate(
                    "com.example.app", candidate, Path(directory)
                )
                self.assertEqual(result.suffix, ".apks")

        command = run.call_args.args[0]
        self.assertEqual(command[:3], [sys.executable, "-m", "src.gplaydl_profile_retry"])
        self.assertEqual(command[3:5], ["download", "com.example.app"])
        self.assertEqual(command[command.index("-v") + 1], "123")
        self.assertEqual(command[command.index("-a") + 1], "arm64")
        self.assertNotIn("--dispenser", command)
        self.assertNotIn("secret-key", command)

    def test_custom_dispenser_is_forwarded_to_upstream_cli(self) -> None:
        with mock.patch.dict(
            os.environ,
            {"GPLAYDL_DISPENSER_URL": "https://play.example.invalid"},
            clear=False,
        ):
            command = aurora_play._linked_gplaydl_command(
                "/usr/bin/gplaydl",
                "com.example.app",
                Path("downloads"),
                None,
            )

        self.assertEqual(
            command[command.index("--dispenser") + 1],
            "https://play.example.invalid",
        )

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
        candidate = VersionCandidate(name="9.8.7", code="987654")
        with mock.patch.dict(os.environ, {"GPLAYDL_API_KEY": "secret-key"}, clear=False):
            with tempfile.TemporaryDirectory() as directory:
                result = aurora_play.download_candidate(
                    "com.example.app",
                    candidate,
                    Path(directory),
                )
                self.assertTrue(result.is_file())
                with zipfile.ZipFile(result) as archive:
                    self.assertIn("base.apk", archive.namelist())
                    self.assertIn("split_config.arm64_v8a.apk", archive.namelist())

        command = run.call_args.args[0]
        self.assertEqual(command[command.index("download") + 1], "com.example.app")
        self.assertEqual(command[command.index("-v") + 1], "987654")
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
                result = aurora_play._download_with_linked_gplaydl(
                    "com.example.app",
                    None,
                    Path(directory),
                )
                self.assertEqual(result.read_bytes(), b"base")

        command = run.call_args.args[0]
        self.assertNotIn("-v", command)
        self.assertEqual(command[command.index("download") + 1], "com.example.app")

    @mock.patch("src.aurora_play._download_with_linked_gplaydl")
    def test_adguard_never_touches_google_play(self, linked: mock.Mock) -> None:
        with self.assertRaises(aurora_play.GooglePlayDisabled):
            aurora_play.download_current("com.adguard.android", Path("."))
        linked.assert_not_called()

    @mock.patch("src.aurora_play.apk_identity.validate_identity")
    @mock.patch("src.aurora_play.shutil.which", return_value="/usr/bin/apkeep")
    @mock.patch("src.aurora_play._run")
    def test_apkeep_fallback_uses_google_play_ini_without_accepting_tos(
        self,
        run: mock.Mock,
        _which: mock.Mock,
        validate_identity: mock.Mock,
    ) -> None:
        observed: dict[str, object] = {}

        def fake_run(command, *, cwd=None, env=None):
            if command == ["/usr/bin/apkeep", "--version"]:
                return mock.Mock(returncode=0, stdout="apkeep 1.0.0\n")
            config = Path(command[command.index("-i") + 1])
            observed["command"] = list(command)
            observed["env"] = dict(env)
            observed["config"] = config.read_text(encoding="utf-8")
            observed["mode"] = stat.S_IMODE(config.stat().st_mode)
            output = Path(command[-1]) / "split-output"
            output.mkdir()
            (output / "base.apk").write_bytes(b"official-google-play-apk")
            return mock.Mock(returncode=0, stdout="downloaded successfully")

        run.side_effect = fake_run
        validate_identity.return_value = ApkIdentity(
            "jp.example.bank",
            "1.2.3",
            "40",
        )
        with (
            mock.patch.dict(
                os.environ,
                {
                    "GPLAY_EMAIL": "secret@example.com",
                    "GPLAY_AAS_TOKEN": "aas_et/secret-token",
                    "GPLAYDL_API_KEY": "secret-api-key",
                },
                clear=False,
            ),
            tempfile.TemporaryDirectory() as directory,
        ):
            result = aurora_play._download_with_apkeep_google_play(
                "jp.example.bank",
                Path(directory),
            )
            self.assertEqual(result.read_bytes(), b"official-google-play-apk")

        command = observed["command"]
        self.assertEqual(command[command.index("-d") + 1], "google-play")
        self.assertEqual(
            command[command.index("-o") + 1],
            "device=px_9a,locale=ja_JP,timezone=Asia/Tokyo,split_apk=true",
        )
        self.assertNotIn("--accept-tos", command)
        rendered_command = " ".join(command)
        self.assertNotIn("secret@example.com", rendered_command)
        self.assertNotIn("aas_et/secret-token", rendered_command)
        self.assertEqual(
            observed["config"],
            "[google]\nemail = secret@example.com\naas_token = aas_et/secret-token\n",
        )
        if os.name != "nt":
            self.assertEqual(observed["mode"], 0o600)
        self.assertNotIn("GPLAY_EMAIL", observed["env"])
        self.assertNotIn("GPLAY_AAS_TOKEN", observed["env"])
        self.assertNotIn("GPLAYDL_API_KEY", observed["env"])
        validate_identity.assert_called_once_with(result, "jp.example.bank", None)

    @mock.patch("src.aurora_play.apk_identity.validate_identity")
    @mock.patch("src.aurora_play.shutil.which", return_value="/usr/bin/apkeep")
    @mock.patch("src.aurora_play._run")
    def test_apkeep_zero_exit_without_apk_is_failure(
        self,
        run: mock.Mock,
        _which: mock.Mock,
        validate_identity: mock.Mock,
    ) -> None:
        run.side_effect = [
            mock.Mock(returncode=0, stdout="apkeep 1.0.0\n"),
            mock.Mock(returncode=0, stdout="Invalid app response. Skipping..."),
        ]
        with (
            mock.patch.dict(
                os.environ,
                {
                    "GPLAY_EMAIL": "secret@example.com",
                    "GPLAY_AAS_TOKEN": "aas_et/secret-token",
                },
                clear=False,
            ),
            tempfile.TemporaryDirectory() as directory,
        ):
            with self.assertRaisesRegex(RuntimeError, "no usable APKs"):
                aurora_play._download_with_apkeep_google_play(
                    "jp.example.bank",
                    Path(directory),
                )
        validate_identity.assert_not_called()

    @mock.patch("src.aurora_play._download_with_linked_gplaydl")
    @mock.patch("src.aurora_play._download_with_apkeep_google_play")
    @mock.patch("src.aurora_play._download_with_playfetch_google_play")
    def test_current_release_prefers_playfetch_google_play(
        self,
        playfetch: mock.Mock,
        apkeep: mock.Mock,
        gplaydl: mock.Mock,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            expected = Path(directory) / "fallback.apk"
            expected.write_bytes(b"official")
            playfetch.return_value = expected
            result = aurora_play.download_current(
                "jp.example.bank",
                Path(directory),
            )

        self.assertIs(result, expected)
        playfetch.assert_called_once_with("jp.example.bank", Path(directory))
        apkeep.assert_not_called()
        gplaydl.assert_not_called()

    @mock.patch("src.aurora_play._download_with_linked_gplaydl")
    @mock.patch("src.aurora_play._download_with_apkeep_google_play")
    @mock.patch("src.aurora_play._download_with_playfetch_google_play")
    def test_current_release_playfetch_failure_uses_apkeep(
        self,
        playfetch: mock.Mock,
        apkeep: mock.Mock,
        gplaydl: mock.Mock,
    ) -> None:
        playfetch.side_effect = RuntimeError("current DFE temporarily unavailable")
        with tempfile.TemporaryDirectory() as directory:
            expected = Path(directory) / "fallback.apk"
            expected.write_bytes(b"official")
            apkeep.return_value = expected
            result = aurora_play.download_current(
                "jp.example.bank",
                Path(directory),
            )

        self.assertIs(result, expected)
        apkeep.assert_called_once_with("jp.example.bank", Path(directory))
        gplaydl.assert_not_called()

    @mock.patch("src.aurora_play._download_with_linked_gplaydl")
    @mock.patch("src.aurora_play._download_with_apkeep_google_play")
    @mock.patch("src.aurora_play._download_with_playfetch_google_play")
    def test_current_release_two_failures_use_gplaydl_profile_retry(
        self,
        playfetch: mock.Mock,
        apkeep: mock.Mock,
        gplaydl: mock.Mock,
    ) -> None:
        playfetch.side_effect = RuntimeError("playfetch unavailable")
        apkeep.side_effect = RuntimeError("apkeep unavailable")
        with tempfile.TemporaryDirectory() as directory:
            expected = Path(directory) / "fallback.apk"
            expected.write_bytes(b"official")
            gplaydl.return_value = expected
            result = aurora_play.download_current(
                "jp.example.bank",
                Path(directory),
            )

        self.assertIs(result, expected)
        gplaydl.assert_called_once_with("jp.example.bank", None, Path(directory))


if __name__ == "__main__":
    unittest.main()
