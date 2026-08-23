import hashlib
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src import aurora_play
from src.apk_identity import ApkIdentity


class PlayfetchGooglePlayTests(unittest.TestCase):
    def test_redacts_url_encoded_email_from_child_diagnostics(self) -> None:
        diagnostic = (
            "POST http://127.0.0.1/api/auth?email="
            "private%40example.com&device=px_9a"
        )
        self.assertEqual(
            aurora_play._secret_safe_text(diagnostic),
            "POST http://127.0.0.1/api/auth?email=[redacted-email]&device=px_9a",
        )

    @mock.patch("src.aurora_play.apk_identity.validate_identity")
    @mock.patch("src.aurora_play.shutil.which", return_value="/usr/bin/playfetch")
    @mock.patch("src.aurora_play._run")
    def test_uses_owner_only_account_store_and_reverifies_play_hashes(
        self,
        run: mock.Mock,
        _which: mock.Mock,
        validate_identity: mock.Mock,
    ) -> None:
        observed: dict[str, object] = {}
        apk = b"official-google-play-base"
        digest = hashlib.sha256(apk).hexdigest()

        def fake_run(command, *, cwd=None, env=None):
            if command == ["/usr/bin/playfetch", "version"]:
                return mock.Mock(returncode=0, stdout="playfetch v0.9.1\n")
            output = Path(command[command.index("-out") + 1])
            (output / "base.apk").write_bytes(apk)
            (output / "manifest.json").write_text(
                json.dumps(
                    {
                        "source": {"kind": "google-play", "account": "secret@example.com"},
                        "app": {
                            "package": "jp.example.bank",
                            "version_code": 40,
                            "version_name": "1.2.3",
                        },
                        "files": [
                            {
                                "name": "base.apk",
                                "size": len(apk),
                                "verified": True,
                                "play_sha256": digest,
                                "local": {"sha256": digest},
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            credentials = Path(command[command.index("-credentials") + 1])
            observed["command"] = list(command)
            observed["env"] = dict(env)
            observed["credentials"] = json.loads(credentials.read_text(encoding="utf-8"))
            observed["mode"] = stat.S_IMODE(credentials.stat().st_mode)
            return mock.Mock(returncode=0, stdout="all files verified")

        run.side_effect = fake_run
        validate_identity.return_value = ApkIdentity("jp.example.bank", "1.2.3", "40")
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
            result = aurora_play._download_with_playfetch_google_play(
                "jp.example.bank", Path(directory)
            )
            self.assertEqual(result.read_bytes(), apk)

        command = observed["command"]
        self.assertEqual(command[:3], ["/usr/bin/playfetch", "pull", "jp.example.bank"])
        self.assertNotIn("-accept-tos", command)
        self.assertIn("-refresh", command)
        self.assertEqual(command[command.index("-profile") + 1], "px_9a")
        self.assertEqual(command[command.index("-locale") + 1], "ja_JP")
        self.assertNotIn("secret@example.com", " ".join(command))
        self.assertNotIn("aas_et/secret-token", " ".join(command))
        self.assertEqual(observed["credentials"]["default"], "ci")
        self.assertEqual(observed["credentials"]["accounts"][0]["region"], "JP")
        if os.name != "nt":
            self.assertEqual(observed["mode"], 0o600)
        self.assertNotIn("GPLAY_EMAIL", observed["env"])
        self.assertNotIn("GPLAY_AAS_TOKEN", observed["env"])
        self.assertNotIn("GPLAYDL_API_KEY", observed["env"])
        validate_identity.assert_called_once_with(result, "jp.example.bank", None)

    def test_rejects_unverified_or_tampered_playfetch_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "base.apk").write_bytes(b"tampered")
            (root / "manifest.json").write_text(
                json.dumps(
                    {
                        "source": {"kind": "google-play"},
                        "app": {"package": "jp.example.bank"},
                        "files": [
                            {
                                "name": "base.apk",
                                "size": 8,
                                "verified": False,
                                "local": {"sha256": "0" * 64},
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(RuntimeError, "did not verify every APK"):
                aurora_play._verified_playfetch_files(
                    root / "manifest.json", "jp.example.bank"
                )


if __name__ == "__main__":
    unittest.main()
