import unittest

from src import aurora_play


class GPlayDlPrimaryLocaleTests(unittest.TestCase):
    def test_primary_gplaydl_requests_japanese_language_split_by_default(self) -> None:
        command = aurora_play._linked_gplaydl_command(
            "gplaydl",
            "com.protonvpn.android",
            __import__("pathlib").Path("/tmp/downloads"),
            None,
        )
        self.assertEqual(command[command.index("-l") + 1], "en-US,ja")

    def test_primary_gplaydl_preserves_explicit_locale_policy(self) -> None:
        import os
        from pathlib import Path
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
