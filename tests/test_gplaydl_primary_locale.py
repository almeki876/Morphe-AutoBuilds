import os
import unittest
from pathlib import Path

from src import aurora_play


# Regression guard: the normal gplaydl path must request the Japanese locale
# split by default so the downloaded payload can contain Japanese resources.
class GPlayDlPrimaryLocaleTests(unittest.TestCase):
    def test_primary_gplaydl_requests_japanese_language_split_by_default(self) -> None:
        old = os.environ.pop("GPLAYDL_LOCALES", None)
        try:
            command = aurora_play._linked_gplaydl_command(
                "gplaydl",
                "com.protonvpn.android",
                Path("/tmp/downloads"),
                None,
            )
            self.assertEqual(command[command.index("-l") + 1], "en-US,ja")
        finally:
            if old is not None:
                os.environ["GPLAYDL_LOCALES"] = old

    def test_primary_gplaydl_preserves_explicit_locale_policy(self) -> None:
        old = os.environ.get("GPLAYDL_LOCALES")
        try:
            os.environ["GPLAYDL_LOCALES"] = "en-US,fr,ja"
            command = aurora_play._linked_gplaydl_command(
                "gplaydl",
                "com.example",
                Path("/tmp/downloads"),
                None,
            )
            self.assertEqual(command[command.index("-l") + 1], "en-US,fr,ja")
        finally:
            if old is None:
                os.environ.pop("GPLAYDL_LOCALES", None)
            else:
                os.environ["GPLAYDL_LOCALES"] = old


if __name__ == "__main__":
    unittest.main()
