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
    Source("MORPHE", "Morphe", "https://github.com/MorpheApp/morphe-patches", "YouTube, YouTube Music"),
    Source("ANDDEA", "Anddea", "https://github.com/anddea/revanced-patches", "YouTube, YouTube Music"),
    Source("RUSHIRANPISE", "rushiranpise", "https://github.com/rushiranpise/morphe-patches", "1.1.1.1, AccuBattery, AdGuard, Adobe Scan, Amazon Shopping, Call Recorder, CamScanner, Countdown Widget, Excel, File Manager, Kahoot!, KineMaster, MEGA, Ninja VPN, SD Maid SE, Speedtest, Windscribe VPN, Word, Yuucho Tuucho, Yuucho Ninsho"),
    Source("HOO_DLES", "hoo-dles", "https://github.com/hoo-dles/morphe-patches", "Prime Video, Duolingo, ibis Paint X, Icon Packer, Smart Launcher, SoundCloud, WPS Office, Crunchyroll, GitHub, Lightroom Mobile, Windy, Xodo, XRecorder"),
    Source("SHAUN_THE_SHEEP_PATCHES", "shaun-the-sheep-patches", "https://github.com/shaun-the-sheep-patches/morphe-patches", "KineStop"),
    Source("ROOKIE", "RookieEnough", "https://github.com/RookieEnough/De-Vanced", "Amazon Music, Disney+, Google News, Google Photos, Google Recorder, Photomath, Adobe Photoshop Mix, Pixiv, Tumblr, Viber"),
    Source("AJSTRICK81", "ajstrick81", "https://github.com/ajstrick81/morphe-androidtv-patches", "Disney+ (Android TV), Netflix, Prime Video (Android TV), Twitch (Android TV)"),
    Source("ANDREWLIANG25", "andrewliang25", "https://github.com/andrewliang25/morphe-patches", "LINE"),
    Source("HOOMANS", "Hoomans", "https://github.com/arandomhooman/hoomans-morphe-patches", "Adobe Acrobat, FolderSync, InShot, Poweramp, Twitch"),
    Source("HXREBORN", "hxreborn", "https://github.com/hxreborn/morphe-patches", "Proton Mail"),
    Source("ICYSYMMETRA", "icysymmetra", "https://github.com/icysymmetra/tiktok-patches-for-morphe", "TikTok"),
    Source("DURGESH0505", "durgesh0505", "https://github.com/durgesh0505/chiggi_morphe_patches", "Threads"),
    Source("MORNING_ENTREE", "Morning-Entree-Patches", "https://github.com/Entree3k/Morning-Entree-Patches", "Gboard, Nova Launcher, Sleep as Android"),
    Source("JASON", "Gboard-patches", "https://github.com/jasonwu1994/Gboard-patches", "Gboard"),
    Source("ADOBO", "adobo", "https://github.com/jkennethcarino/adobo", "Gboard"),
    Source("PARESH", "Paresh-Maheshwari", "https://gitlab.com/Paresh-Maheshwari/paresh-patches", "Fing, Proton VPN"),
    Source("DH6K", "dh6k", "https://github.com/dh6k/morphe-patches", "Brave, Brave Beta, Brave Nightly"),
    Source("BHOLEY", "BholeyKaBhakt", "https://github.com/BholeyKaBhakt/android-patches-xtra", "Speedtest"),
    Source("FLUFFY", "Fluffy", "https://github.com/rabilrbl/fluffy-patches", "Alarmy"),
    Source("QUANTRO", "Quantro", "https://github.com/Quantro100/Morphe-patches", "AliExpress"),
    Source("LAIN", "Lain-Patches", "https://github.com/kiraio-moe/Lain-Patches", "iLovePDF"),
)

MAX_RELEASE_NOTES_LENGTH = 120_000



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


def _fit_release_notes(text: str) -> str:
    if len(text) <= MAX_RELEASE_NOTES_LENGTH:
        return text
    suffix = "\n\n[Additional release details omitted to fit GitHub's release-notes limit.]\n"
    return text[: MAX_RELEASE_NOTES_LENGTH - len(suffix)] + suffix


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
    output.write_text(_fit_release_notes("".join(parts)), encoding="utf-8")
    print(f"Release notes generated: {output}")


if __name__ == "__main__":
    main()
