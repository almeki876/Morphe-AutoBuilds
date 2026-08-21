"""Google Play APK downloads using Aurora-style anonymous authentication.

Google Play is the preferred APK origin.  The downloader uses a pinned JVM
implementation of AuroraOSS GPlayApi and asks Google Play for the complete
file set (base APK plus split APKs).  A requested versionCode is passed to
PurchaseHelper.purchase(); when no code is supplied, Google Play's current
AppDetails versionCode is used by the helper.

The external downloader source is pinned to a commit and patched locally at
build time only to accept GPLAY_VERSION_CODE. APK identity is still validated
by the caller before the result is cached or patched.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

from src.versioning import VersionCandidate

GPLAY_DOWNLOADER_REPOSITORY = "https://github.com/ikolomiko/gplay-downloader.git"
GPLAY_DOWNLOADER_COMMIT = "2d7998c6f5a8a13211dd05231500f746ac8e3942"
GPLAY_DOWNLOADER_DIR = Path("tools/google-play-downloader")
GPLAY_DOWNLOADER_JAR = (
    GPLAY_DOWNLOADER_DIR / "app/build/libs/gplay-downloader-with-dependencies.jar"
)

_PURCHASE_ORIGINAL = ".purchase(app.packageName, app.versionCode, app.offerType)"
_PURCHASE_EXACT = (
    ".purchase(app.packageName, "
    "System.getenv(\"GPLAY_VERSION_CODE\")?.toLongOrNull() ?: app.versionCode, "
    "app.offerType)"
)


def _run(command: list[str], *, cwd: Path | None = None, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        env=env,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )


def _ensure_downloader() -> Path:
    """Build a pinned JVM GPlayApi downloader with exact-versionCode support."""
    if GPLAY_DOWNLOADER_JAR.is_file() and GPLAY_DOWNLOADER_JAR.stat().st_size > 0:
        return GPLAY_DOWNLOADER_JAR

    if GPLAY_DOWNLOADER_DIR.exists():
        shutil.rmtree(GPLAY_DOWNLOADER_DIR)
    GPLAY_DOWNLOADER_DIR.parent.mkdir(parents=True, exist_ok=True)

    clone = _run(
        [
            "git",
            "clone",
            "--no-checkout",
            "--filter=blob:none",
            GPLAY_DOWNLOADER_REPOSITORY,
            str(GPLAY_DOWNLOADER_DIR),
        ]
    )
    if clone.returncode != 0:
        raise RuntimeError(f"could not clone pinned Google Play downloader: {clone.stdout[-1000:]}")

    checkout = _run(["git", "checkout", GPLAY_DOWNLOADER_COMMIT], cwd=GPLAY_DOWNLOADER_DIR)
    if checkout.returncode != 0:
        raise RuntimeError(f"could not checkout pinned Google Play downloader: {checkout.stdout[-1000:]}")

    source = GPLAY_DOWNLOADER_DIR / "app/src/main/kotlin/gplay/downloader/Downloader.kt"
    text = source.read_text(encoding="utf-8")
    if _PURCHASE_ORIGINAL not in text:
        raise RuntimeError("pinned Google Play downloader purchase call changed unexpectedly")
    source.write_text(text.replace(_PURCHASE_ORIGINAL, _PURCHASE_EXACT, 1), encoding="utf-8")

    gradlew = GPLAY_DOWNLOADER_DIR / "gradlew"
    gradlew.chmod(gradlew.stat().st_mode | 0o111)
    build = _run(["./gradlew", "--no-daemon", "shadowJar"], cwd=GPLAY_DOWNLOADER_DIR)
    if build.returncode != 0 or not GPLAY_DOWNLOADER_JAR.is_file():
        raise RuntimeError(f"could not build Google Play downloader: {build.stdout[-2000:]}")
    return GPLAY_DOWNLOADER_JAR


def _package_apks(apk_files: list[Path], package: str, output_dir: Path) -> Path:
    """Return one patcher-compatible input containing all Play-delivered APKs."""
    if not apk_files:
        raise IOError(f"Google Play produced no APK files for {package}")
    if len(apk_files) == 1:
        target = output_dir / f"{package}-google-play.apk"
        shutil.copy2(apk_files[0], target)
        return target

    target = output_dir / f"{package}-google-play.apks"
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        used: set[str] = set()
        for index, source in enumerate(sorted(apk_files, key=lambda p: p.name)):
            name = source.name
            if name in used:
                name = f"split-{index}-{name}"
            used.add(name)
            archive.write(source, name)
    return target


def download_candidate(
    package: str,
    candidate: VersionCandidate | None,
    output_dir: Path | None = None,
) -> Path:
    """Download package from Google Play, optionally purchasing an exact versionCode.

    If ``candidate.code`` is present it is supplied directly to GPlayApi's
    PurchaseHelper. Otherwise the downloader purchases the versionCode returned
    by Google Play's current AppDetails response. The caller validates versionName
    and versionCode against ``candidate`` before accepting the result.
    """
    output_dir = output_dir or Path(".")
    output_dir.mkdir(parents=True, exist_ok=True)
    jar = _ensure_downloader()

    with tempfile.TemporaryDirectory(prefix="google-play-", dir=output_dir) as tmp:
        tmp_dir = Path(tmp)
        app_ids = tmp_dir / "apps.txt"
        app_ids.write_text(package + "\n", encoding="utf-8")
        downloads = tmp_dir / "downloads"
        downloads.mkdir()

        env = os.environ.copy()
        if candidate and candidate.code:
            env["GPLAY_VERSION_CODE"] = str(candidate.code)
            logging.info(
                "🌌 Google Play first: package=%s exact-versionCode=%s",
                package,
                candidate.code,
            )
        else:
            env.pop("GPLAY_VERSION_CODE", None)
            logging.info(
                "🌌 Google Play first: package=%s current Play versionCode",
                package,
            )

        result = _run(
            [
                "java",
                "-jar",
                str(jar.resolve()),
                "-a",
                str(app_ids.resolve()),
                "-o",
                str(downloads.resolve()),
                "-d",
            ],
            env=env,
        )
        if result.returncode != 0:
            raise RuntimeError("Google Play downloader exited non-zero: " + result.stdout[-1500:])

        package_dir = downloads / package
        apk_files = list(package_dir.glob("*.apk")) if package_dir.is_dir() else []
        if not apk_files:
            apk_files = list(downloads.glob("*.apk"))
        if not apk_files:
            tail = "\n".join(result.stdout.splitlines()[-20:])
            raise IOError(
                f"Google Play produced no APK files for {package}"
                + (f": {tail}" if tail else "")
            )

        return _package_apks(apk_files, package, output_dir)


def download_current(package: str, output_dir: Path | None = None) -> Path:
    """Compatibility wrapper for callers that want the current Play release."""
    return download_candidate(package, None, output_dir)
