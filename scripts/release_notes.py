"""Generate integrated release notes from workflow environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class Source:
    key: str
    label: str
    url: str
    apps: str


SOURCES = (
    Source(
        "MORPHE",
        "Morphe",
        "https://github.com/MorpheApp/morphe-patches",
        "YouTube, YouTube Music",
    ),
    Source(
        "ANDDEA",
        "Anddea",
        "https://github.com/anddea/revanced-patches",
        "YouTube, YouTube Music",
    ),
    Source(
        "HOO",
        "rushiranpise",
        "https://github.com/rushiranpise/morphe-patches",
        (
            "AdGuard, Prime Video, Duolingo, ibis Paint X, Icon Packer, Nova, "
            "Proton VPN, Smart Launcher, SoundCloud, WPS Office, Crunchyroll, "
            "GitHub, Windy, Xodo, XRecorder, "
            "ゆうちょ通帳, ゆうちょ認証, Adobe Scan, AccuBattery, KineStop, "
            "KineMaster, Kahoot!, Ninja VPN, Countdown Widget, CamScanner, Call Recorder"
        ),
    ),
    Source(
        "HOOMANS",
        "Hoomans",
        "https://github.com/arandomhooman/hoomans-morphe-patches",
        "Adobe Acrobat, FolderSync, InShot, Poweramp",
    ),
    Source(
        "QUANTRO",
        "Quantro",
        "https://github.com/Quantro100/Morphe-patches",
        "AliExpress",
    ),
    Source(
        "FLUFFY",
        "Fluffy",
        "https://github.com/rabilrbl/fluffy-patches",
        "Alarmy",
    ),
    Source(
        "ROOKIE",
        "RookieEnough",
        "https://github.com/RookieEnough/De-Vanced",
        (
            "Proton Mail, Disney+, Photomath, Pixiv, Adobe Photoshop Mix, "
            "Amazon Shopping, Google News, Google Photos, Google Recorder, "
            "Threads, TikTok, Tumblr, Twitch, Viber, Amazon Music"
        ),
    ),
    Source(
        "TOSOX",
        "Tosox",
        "https://github.com/Tosox/revanced-patches",
        "MEGA",
    ),
    Source(
        "YUZU",
        "YuzuMikan404",
        "https://github.com/matchadaisuke/morphe-patches",
        "",
    ),
    Source(
        "DROPPED",
        "Dropped-Patches",
        "https://github.com/indrastorms/Dropped-Patches",
        "",
    ),
    Source(
        "LAIN",
        "Lain-Patches",
        "https://github.com/kiraio-moe/Lain-Patches",
        "iLovePDF",
    ),
    Source(
        "JASON",
        "Gboard-patches",
        "https://github.com/jasonwu1994/Gboard-patches",
        "Gboard",
    ),
    Source(
        "ADOBO",
        "adobo",
        "https://github.com/jkennethcarino/adobo",
        "Gboard",
    ),
    Source(
        "MORNING_ENTREE",
        "Morning-Entree-Patches",
        "https://github.com/Entree3k/Morning-Entree-Patches",
        "Gboard",
    ),
    Source(
        "AJSTRICK81",
        "ajstrick81",
        "https://github.com/ajstrick81/morphe-androidtv-patches",
        "Prime Video (Android TV), Netflix, Disney+",
    ),
    Source(
        "ANDREWLIANG25",
        "andrewliang25",
        "https://github.com/andrewliang25/morphe-patches",
        "LINE",
    ),
    Source(
        "HOO_DLES",
        "hoo-dles",
        "https://github.com/hoo-dles/morphe-patches",
        "Lightroom Mobile",
    ),
    Source(
        "BHOLEY",
        "BholeyKaBhakt",
        "https://github.com/BholeyKaBhakt/android-patches-xtra",
        "Speedtest",
    ),
    Source(
        "PARESH",
        "Paresh-Maheshwari",
        "https://gitlab.com/Paresh-Maheshwari/paresh-patches",
        "Fing",
    ),
    Source(
        "DH6K",
        "dh6k",
        "https://github.com/dh6k/morphe-patches",
        "Brave",
    ),
)



def _enabled(name: str) -> bool:
    val = os.environ.get(name, "").casefold() == "true"
    if not val and name.startswith("ANDDEA_"):
        alias = name.replace("ANDDEA_", "REVANCED_ANDDEA_")
        val = os.environ.get(alias, "").casefold() == "true"
    return val


def _status(updated: bool, forced: bool) -> str:
    if updated and forced:
        return "Patches and APK updated"
    if updated:
        return "Patches updated"
    return "APK updated"


def render() -> str:
    now = datetime.now(ZoneInfo("Asia/Tokyo")).strftime("%Y-%m-%d %H:%M JST")
    lines = [
        now,
        "",
        "| Source | Version | Apps | Status |",
        "| --- | --- | --- | --- |",
    ]
    for source in SOURCES:
        updated = _enabled(f"{source.key}_UPDATED")
        forced = _enabled(f"{source.key}_FORCE")
        if not updated and not forced:
            continue
        tag = os.environ.get(f"{source.key}_TAG", "unknown")
        lines.append(
            f"| [{source.label}]({source.url}) | "
            f"[{tag}]({source.url}/releases/tag/{tag}) | "
            f"{source.apps} | {_status(updated, forced)} |"
        )
    return "\n".join(lines) + "\n"


def main() -> None:
    output = Path(os.environ.get("RELEASE_NOTES_PATH", "release_notes.md"))
    parts = [render()]
    for name in (
        "build_status.md",
        "virustotal_base_results.md",
    ):
        path = Path(name)
        if path.is_file() and path.stat().st_size:
            parts.append(path.read_text(encoding="utf-8"))
    output.write_text("".join(parts), encoding="utf-8")
    print(f"Release notes generated: {output}")


if __name__ == "__main__":
    main()
