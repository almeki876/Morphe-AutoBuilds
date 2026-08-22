import unittest

from src.versioning import discovered_version_code


class PatchVersionCodeTests(unittest.TestCase):
    def test_known_google_play_codes_for_open_issue_releases(self) -> None:
        self.assertEqual(
            discovered_version_code(
                "com.amazon.mShop.android.shopping", "32.13.2.100"
            ),
            "1241320216",
        )
        self.assertEqual(
            discovered_version_code("com.adobe.reader", "26.7.1.47181"),
            "1931947181",
        )


if __name__ == "__main__":
    unittest.main()
