import unittest
from pathlib import Path


class OpenIssueFixTests(unittest.TestCase):
    def test_twitch_tv_uses_only_v30_compatible_ad_patch(self) -> None:
        patch_file = Path("patches/twitch-android-tv-ajstrick81.txt")
        self.assertEqual(patch_file.read_text(encoding="utf-8").splitlines(), ["Skip ads"])


if __name__ == "__main__":
    unittest.main()
