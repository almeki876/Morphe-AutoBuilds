import os
import unittest
from pathlib import Path
from unittest import mock

from src import aurora_play


class GPlayDlLocaleShimTests(unittest.TestCase):
    def test_gplaydl_subprocess_gets_japanese_play_locale_by_default(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            env = aurora_play._gplaydl_runtime_env(
                ["/usr/local/bin/gplaydl", "download", "com.example.app"]
            )

        self.assertIsNotNone(env)
        assert env is not None
        self.assertEqual(env["GPLAYDL_PLAY_LOCALE"], "ja-JP")
        self.assertEqual(
            Path(env["PYTHONPATH"].split(os.pathsep)[0]),
            aurora_play.GPLAYDL_SHIM_DIR,
        )

    def test_explicit_play_locale_is_preserved(self) -> None:
        with mock.patch.dict(
            os.environ,
            {"GPLAYDL_PLAY_LOCALE": "ja_JP", "PYTHONPATH": "/existing"},
            clear=True,
        ):
            env = aurora_play._gplaydl_runtime_env(
                ["gplaydl", "download", "com.example.app"]
            )

        self.assertIsNotNone(env)
        assert env is not None
        self.assertEqual(env["GPLAYDL_PLAY_LOCALE"], "ja_JP")
        self.assertEqual(env["PYTHONPATH"].split(os.pathsep)[1], "/existing")

    def test_non_gplaydl_subprocess_is_not_modified(self) -> None:
        self.assertIsNone(aurora_play._gplaydl_runtime_env(["python", "-V"]))


if __name__ == "__main__":
    unittest.main()
