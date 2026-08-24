import json
from pathlib import Path


def test_youtube_music_unsupported_recommended_patches_are_disabled():
    data = json.loads(Path("my-patch-config.json").read_text(encoding="utf-8"))
    entry = next(
        item for item in data["patch_list"]
        if item.get("app_name") == "youtube-music"
        and item.get("source") == "revanced-anddea"
    )
    expected = {
        "Enable compact dialog",
        "Hide handle",
        "Enable smooth transition animation",
        "Restore old comments popup panels",
        "Spoof app version",
    }
    assert expected <= set(entry.get("disable") or [])
