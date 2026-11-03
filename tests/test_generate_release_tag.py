from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

from scripts import generate_release_tag


class GenerateReleaseTagTests(unittest.TestCase):
    def test_generates_four_part_numeric_tag_in_jst(self) -> None:
        class FixedDateTime(datetime):
            @classmethod
            def now(cls, tz: ZoneInfo | None = None) -> datetime:
                return cls(2026, 11, 3, 20, 28, tzinfo=timezone.utc)

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "github-output"
            with (
                mock.patch.object(generate_release_tag, "datetime", FixedDateTime),
                mock.patch.object(generate_release_tag, "ZoneInfo") as zone_info,
                mock.patch.dict("os.environ", {"GITHUB_OUTPUT": str(output)}),
            ):
                zone_info.return_value = timezone.utc
                self.assertEqual(generate_release_tag.main(), 0)

            zone_info.assert_called_once_with("Asia/Tokyo")
            self.assertEqual(output.read_text(encoding="utf-8"), "release_tag=2026.11.03.2028\n")
            numeric_parts = output.read_text(encoding="utf-8").split("=", 1)[1].strip().split(".")
            self.assertEqual(len(numeric_parts), 4)
            self.assertTrue(all(part.isdigit() for part in numeric_parts))
            self.assertGreater(
                tuple(map(int, numeric_parts)),
                (2026, 11, 3, 1923),
            )


if __name__ == "__main__":
    unittest.main()
