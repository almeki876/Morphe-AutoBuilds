import hashlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src import persisted_gplaydl_dispenser as persisted


class PersistedGPlayDlDispenserTests(unittest.TestCase):
    def test_state_keys_are_stable_and_domain_separated(self) -> None:
        with mock.patch.dict(os.environ, {"GPLAYDL_API_KEY": "example-secret"}, clear=False):
            database_key = persisted._server_encryption_key()
            snapshot_key = persisted._snapshot_password()

        self.assertEqual(len(database_key), 64)
        self.assertEqual(len(snapshot_key), 64)
        self.assertNotEqual(database_key, snapshot_key)

    def test_ci_api_key_hash_matches_upstream_sha256_lookup(self) -> None:
        with mock.patch.dict(os.environ, {"GPLAYDL_API_KEY": "example-secret"}, clear=False):
            actual = persisted._ci_api_key_hash()

        expected = hashlib.sha256(b"example-secret").hexdigest()
        self.assertEqual(actual, expected)

    def test_configured_state_file_requires_existing_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / persisted.STATE_FILENAME
            with mock.patch.dict(os.environ, {"GPLAYDL_STATE_FILE": str(state)}, clear=False):
                self.assertIsNone(persisted.configured_state_file())
                state.write_bytes(b"encrypted")
                self.assertEqual(persisted.configured_state_file(), state)

    def test_cache_miss_does_not_replace_hosted_path(self) -> None:
        with mock.patch.dict(os.environ, {"GPLAYDL_STATE_FILE": ""}, clear=False):
            self.assertFalse(persisted.ensure_running())


if __name__ == "__main__":
    unittest.main()
