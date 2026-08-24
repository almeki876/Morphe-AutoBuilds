import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class AnddeaBrandingConfigTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        payload = json.loads((ROOT / "my-patch-config.json").read_text(encoding="utf-8"))
        cls.entries = {
            (item.get("app_name"), item.get("source")): item
            for item in payload.get("patch_list", [])
            if isinstance(item, dict)
        }

    def _entry(self, app: str) -> dict:
        return self.entries[(app, "revanced-anddea")]

    def test_youtube_uses_merged_branding_patch(self) -> None:
        entry = self._entry("youtube")
        self.assertEqual(entry.get("required"), ["Custom branding for YouTube"])
        self.assertIn("Custom branding for YouTube", entry.get("force_enable", []))
        options = {(item["patch"], item["key"]): item["value"] for item in entry.get("options", [])}
        self.assertEqual(options[("Custom branding for YouTube", "customName")], "RVA")
        self.assertIn(("Custom branding for YouTube", "customIcon"), options)
        self.assertFalse(any("Custom branding name for YouTube" == patch for patch, _ in options))
        self.assertFalse(any("Custom header for YouTube" == patch for patch, _ in options))

    def test_youtube_music_uses_merged_branding_patch(self) -> None:
        entry = self._entry("youtube-music")
        self.assertEqual(entry.get("required"), ["Custom branding for YouTube Music"])
        self.assertIn("Custom branding for YouTube Music", entry.get("force_enable", []))
        options = {(item["patch"], item["key"]): item["value"] for item in entry.get("options", [])}
        self.assertEqual(options[("Custom branding for YouTube Music", "customName")], "RVA Music")
        self.assertIn(("Custom branding for YouTube Music", "customIcon"), options)
        self.assertFalse(any("Custom branding name for YouTube Music" == patch for patch, _ in options))
        self.assertFalse(any("Custom header for YouTube Music" == patch for patch, _ in options))


if __name__ == "__main__":
    unittest.main()
