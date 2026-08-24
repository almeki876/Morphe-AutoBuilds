import hashlib
import json
import struct
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "my-patch-config.json"
ASSET_ROOT = ROOT / "patch-assets" / "anddea"
PROVENANCE_PATH = ASSET_ROOT / "PROVENANCE.json"


class AnddeaCustomIconTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        cls.provenance = json.loads(PROVENANCE_PATH.read_text(encoding="utf-8"))

    def _options(self, app_name: str) -> list[dict]:
        entries = [
            entry
            for entry in self.config["patch_list"]
            if entry.get("app_name") == app_name
            and entry.get("source") == "revanced-anddea"
        ]
        self.assertEqual(len(entries), 1)
        return entries[0]["options"]

    def test_icon_and_name_options_use_current_anddea_keys(self) -> None:
        youtube_options = self._options("youtube")
        music_options = self._options("youtube-music")

        self.assertIn(
            {
                "patch": "Custom branding for YouTube",
                "key": "appIcon",
                "value": "patch-assets/anddea/youtube/xisr_evergreen",
            },
            youtube_options,
        )
        self.assertIn(
            {
                "patch": "Custom branding for YouTube",
                "key": "customName",
                "value": "RVA",
            },
            youtube_options,
        )
        self.assertIn(
            {
                "patch": "Custom branding for YouTube Music",
                "key": "appIcon",
                "value": "patch-assets/anddea/youtube-music/xisr_yellow",
            },
            music_options,
        )
        self.assertIn(
            {
                "patch": "Custom branding for YouTube Music",
                "key": "customName",
                "value": "RVA Music",
            },
            music_options,
        )

        all_options = youtube_options + music_options
        retired_patches = {
            "Custom branding icon for YouTube",
            "Custom branding icon for YouTube Music",
            "Custom branding name for YouTube",
            "Custom branding name for YouTube Music",
            "Custom header for YouTube",
            "Custom header for YouTube Music",
        }
        self.assertFalse(any(option.get("patch") in retired_patches for option in all_options))
        self.assertFalse(
            any(
                option.get("key") == "customIcon"
                for option in all_options
            )
        )

    def test_custom_icon_folders_match_anddea_copy_contract(self) -> None:
        expected_dimensions = {
            "mdpi": 108,
            "hdpi": 162,
            "xhdpi": 216,
            "xxhdpi": 324,
            "xxxhdpi": 432,
        }
        icon_paths = [
            "patch-assets/anddea/youtube/xisr_evergreen",
            "patch-assets/anddea/youtube-music/xisr_yellow",
        ]

        for relative_icon_path in icon_paths:
            icon_path = (ROOT / relative_icon_path).resolve()
            icon_path.relative_to(ROOT.resolve())
            self.assertTrue(icon_path.is_dir())

            for density, expected_dimension in expected_dimensions.items():
                density_path = icon_path / f"mipmap-{density}"
                self.assertTrue(density_path.is_dir())
                for file_name in (
                    "morphe_adaptive_background_custom.png",
                    "morphe_adaptive_foreground_custom.png",
                ):
                    image = (density_path / file_name).read_bytes()
                    self.assertEqual(image[:8], b"\x89PNG\r\n\x1a\n")
                    self.assertEqual(image[12:16], b"IHDR")
                    width, height = struct.unpack(">II", image[16:24])
                    self.assertEqual((width, height), (expected_dimension,) * 2)

            drawable_path = icon_path / "drawable"
            monochrome = drawable_path / "morphe_adaptive_monochrome_custom.xml"
            notification = drawable_path / "morphe_notification_icon_custom.xml"
            self.assertFalse(monochrome.exists())
            ET.parse(notification)

    def test_vendored_assets_match_pinned_upstream_checksums(self) -> None:
        upstream = self.provenance["upstream"]
        self.assertEqual(upstream["release_tag"], "v4.3.0-dev.2")
        self.assertEqual(
            upstream["commit"],
            "288ce738437c20e3887641f3a0eb367a79099e77",
        )

        recorded_hashes = self.provenance["sha256"]
        actual_files = {
            path.relative_to(ASSET_ROOT).as_posix()
            for path in ASSET_ROOT.rglob("*")
            if path.is_file() and path != PROVENANCE_PATH
        }
        self.assertEqual(actual_files, set(recorded_hashes))

        for relative_path, expected_hash in recorded_hashes.items():
            digest = hashlib.sha256((ASSET_ROOT / relative_path).read_bytes()).hexdigest()
            self.assertEqual(digest, expected_hash, relative_path)


if __name__ == "__main__":
    unittest.main()
