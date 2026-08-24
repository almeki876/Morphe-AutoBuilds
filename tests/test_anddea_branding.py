import json
import unittest
from pathlib import Path


class AnddeaBrandingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        payload = json.loads(Path("my-patch-config.json").read_text(encoding="utf-8"))
        cls.entries = {
            (item["app_name"], item["source"]): item
            for item in payload["patch_list"]
        }

    def _option_value(self, app: str, key: str) -> str:
        entry = self.entries[(app, "revanced-anddea")]
        patch = (
            "Custom branding for YouTube"
            if app == "youtube"
            else "Custom branding for YouTube Music"
        )
        for option in entry.get("options", []):
            if option.get("patch") == patch and option.get("key") == key:
                return option["value"]
        self.fail(f"missing Anddea branding option {app}:{key}")

    def test_youtube_is_named_rva_and_branding_patch_is_required(self) -> None:
        entry = self.entries[("youtube", "revanced-anddea")]
        patch = "Custom branding for YouTube"
        self.assertEqual(self._option_value("youtube", "customName"), "RVA")
        self.assertIn(patch, entry.get("force_enable", []))
        self.assertIn(patch, entry.get("required", []))

    def test_youtube_music_is_named_rva_music_and_branding_patch_is_required(self) -> None:
        entry = self.entries[("youtube-music", "revanced-anddea")]
        patch = "Custom branding for YouTube Music"
        self.assertEqual(self._option_value("youtube-music", "customName"), "RVA Music")
        self.assertIn(patch, entry.get("force_enable", []))
        self.assertIn(patch, entry.get("required", []))


if __name__ == "__main__":
    unittest.main()
