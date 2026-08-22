import base64
import importlib.util
import json
import secrets
import unittest
from pathlib import Path
from unittest import mock

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from nacl.public import PrivateKey, SealedBox


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "register_google_play_secrets.py"
SPEC = importlib.util.spec_from_file_location("register_google_play_secrets", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
registration = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(registration)


class RegisterGooglePlaySecretsTests(unittest.TestCase):
    def test_normalize_email_is_generic(self) -> None:
        self.assertEqual(
            registration._normalize_email("  Example.User@GMAIL.COM  "),
            "example.user@gmail.com",
        )
        with self.assertRaisesRegex(RuntimeError, "valid Google account email"):
            registration._normalize_email("not-an-email")

    def test_decrypts_upstream_dispenser_aes_gcm_format(self) -> None:
        key = secrets.token_bytes(32)
        nonce = secrets.token_bytes(12)
        token = "aas_et/" + "x" * 64
        ciphertext = nonce + AESGCM(key).encrypt(nonce, token.encode("utf-8"), None)

        self.assertEqual(
            registration._decrypt_aas_token(key.hex(), ciphertext.hex()),
            token,
        )

    def test_rejects_non_aas_plaintext_after_decryption(self) -> None:
        key = secrets.token_bytes(32)
        nonce = secrets.token_bytes(12)
        ciphertext = nonce + AESGCM(key).encrypt(nonce, b"not-an-aas-token", None)

        with self.assertRaisesRegex(RuntimeError, "not a valid AAS token"):
            registration._decrypt_aas_token(key.hex(), ciphertext.hex())

    def test_repository_secret_payload_contains_only_sealed_ciphertext(self) -> None:
        private_key = PrivateKey.generate()
        public_key = {
            "key_id": "test-key-id",
            "key": base64.b64encode(bytes(private_key.public_key)).decode("ascii"),
        }
        plaintext = "aas_et/" + "s" * 64

        with mock.patch.object(registration, "_github_request", return_value={}) as request:
            registration._set_repository_secret(
                "owner/repo",
                "GPLAY_AAS_TOKEN",
                plaintext,
                "admin-token",
                public_key,
            )

        method, path, admin_token, payload = request.call_args.args
        self.assertEqual(method, "PUT")
        self.assertEqual(path, "/repos/owner/repo/actions/secrets/GPLAY_AAS_TOKEN")
        self.assertEqual(admin_token, "admin-token")
        self.assertNotIn(plaintext, json.dumps(payload))
        self.assertEqual(payload["key_id"], "test-key-id")

        encrypted = base64.b64decode(payload["encrypted_value"])
        recovered = SealedBox(private_key).decrypt(encrypted).decode("utf-8")
        self.assertEqual(recovered, plaintext)

    def test_query_registered_account_matches_in_python_without_psql_variables(self) -> None:
        result = mock.Mock(
            returncode=0,
            stdout=(
                "other@example.com\t0011\n"
                "MorpheAutoBuilds@GMAIL.COM\taabbcc\n"
            ),
        )
        with mock.patch.object(registration, "_run", return_value=result) as run:
            row = registration._query_registered_account(
                "registration-db", "morpheautobuilds@gmail.com"
            )

        self.assertEqual(row, ("MorpheAutoBuilds@GMAIL.COM", "aabbcc"))
        command = run.call_args.args[0]
        self.assertNotIn("-v", command)
        self.assertNotIn("expected_email", command[-1])

    def test_query_registered_account_surfaces_database_errors(self) -> None:
        result = mock.Mock(returncode=2, stdout="database query failed")
        with mock.patch.object(registration, "_run", return_value=result):
            with self.assertRaisesRegex(RuntimeError, "temporary registration database"):
                registration._query_registered_account(
                    "registration-db", "morpheautobuilds@gmail.com"
                )


if __name__ == "__main__":
    unittest.main()
