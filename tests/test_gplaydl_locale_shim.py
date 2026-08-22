import os
import unittest
from pathlib import Path
from unittest import mock

from src import aurora_play


class GPlayDlDiagnosticShimTests(unittest.TestCase):
    def test_gplaydl_subprocess_loads_diagnostic_shim(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            env = aurora_play._gplaydl_runtime_env(
                ["/usr/local/bin/gplaydl", "download", "com.example.app"]
            )

        self.assertIsNotNone(env)
        assert env is not None
        self.assertEqual(
            Path(env["PYTHONPATH"].split(os.pathsep)[0]),
            aurora_play.GPLAYDL_SHIM_DIR,
        )
        self.assertNotIn("GPLAYDL_PLAY_LOCALE", env)

    def test_existing_pythonpath_is_preserved(self) -> None:
        with mock.patch.dict(os.environ, {"PYTHONPATH": "/existing"}, clear=True):
            env = aurora_play._gplaydl_runtime_env(
                ["gplaydl", "download", "com.example.app"]
            )

        self.assertIsNotNone(env)
        assert env is not None
        self.assertEqual(env["PYTHONPATH"].split(os.pathsep)[1], "/existing")

    def test_non_gplaydl_subprocess_is_not_modified(self) -> None:
        self.assertIsNone(aurora_play._gplaydl_runtime_env(["python", "-V"]))


if __name__ == "__main__":
    unittest.main()
