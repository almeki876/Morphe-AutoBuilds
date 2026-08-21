"""Google Play APK downloads through the repo-local Aurora-style gplaydl CLI.

APK bodies should come from Google Play whenever possible. gplaydl performs
Aurora-style anonymous dispenser authentication, creates a GPlayApi session,
purchases an exact versionCode when supplied, and downloads the complete Play
file set (base plus split APKs). The caller validates package/version identity
before accepting or caching the result.
"""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

from src.versioning import VersionCandidate

GPLAYDL_PROJECT = Path("tools/gplaydl/pom.xml")
GPLAYDL_SOURCE_ROOT = Path("tools/gplaydl/src")
GPLAYDL_JAR = Path("tools/gplaydl/target/gplaydl-1.0-SNAPSHOT-all.jar")
GPLAYDL_FINGERPRINT = Path("tools/gplaydl/target/gplaydl-source.sha256")
DEFAULT_AURORA_USER_AGENT = "com.aurora.store-4.8.4-76"

# Apps that are intentionally distributed from an upstream GitHub release
# rather than Google Play. Keep this list narrow and explicit.
GITHUB_ONLY_PACKAGES = frozenset({"com.adguard.android"})


class GooglePlayDisabled(RuntimeError):
    """Raised when repository policy forbids Google Play for an app."""


def google_play_enabled(package: str) -> bool:
    """Return whether repository policy allows contacting Google Play."""
    return package not in GITHUB_ONLY_PACKAGES


def _run(
    command: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        env=env,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )


def _gplaydl_source_fingerprint() -> str:
    """Hash every checked-in input that changes the repo-local gplaydl binary."""
    if not GPLAYDL_PROJECT.is_file():
        raise FileNotFoundError(f"gplaydl project not found: {GPLAYDL_PROJECT}")

    digest = hashlib.sha256()
    inputs = [GPLAYDL_PROJECT]
    if GPLAYDL_SOURCE_ROOT.is_dir():
        inputs.extend(sorted(path for path in GPLAYDL_SOURCE_ROOT.rglob("*") if path.is_file()))
    for path in inputs:
        digest.update(path.as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _ensure_downloader() -> Path:
    """Build the checked-in JVM CLI and never trust a stale restored JAR."""
    fingerprint = _gplaydl_source_fingerprint()
    cached_fingerprint = ""
    if GPLAYDL_FINGERPRINT.is_file():
        cached_fingerprint = GPLAYDL_FINGERPRINT.read_text(encoding="utf-8").strip()

    if (
        GPLAYDL_JAR.is_file()
        and GPLAYDL_JAR.stat().st_size > 0
        and cached_fingerprint == fingerprint
    ):
        return GPLAYDL_JAR

    if shutil.which("mvn") is None:
        raise FileNotFoundError("mvn is required to build the repo-local gplaydl CLI")

    logging.info("🔧 Building repo-local gplaydl because the cached binary is missing or stale")
    result = _run(
        ["mvn", "-q", "-f", str(GPLAYDL_PROJECT), "-DskipTests", "package"]
    )
    if result.returncode != 0 or not GPLAYDL_JAR.is_file():
        tail = "\n".join((result.stdout or "").splitlines()[-50:])
        raise RuntimeError(f"could not build repo-local gplaydl CLI: {tail}")

    GPLAYDL_FINGERPRINT.parent.mkdir(parents=True, exist_ok=True)
    GPLAYDL_FINGERPRINT.write_text(fingerprint + "\n", encoding="utf-8")
    return GPLAYDL_JAR


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
        for index, source in enumerate(sorted(apk_files, key=lambda item: item.name)):
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
    """Download a Play release, purchasing ``candidate.code`` when known."""
    if not google_play_enabled(package):
        raise GooglePlayDisabled(
            f"Google Play is disabled by repository policy for {package}; use GitHub"
        )

    output_dir = output_dir or Path(".")
    output_dir.mkdir(parents=True, exist_ok=True)
    jar = _ensure_downloader()

    with tempfile.TemporaryDirectory(prefix="google-play-", dir=output_dir) as tmp:
        downloads = Path(tmp) / "downloads"
        downloads.mkdir()
        command = [
            "java",
            "-jar",
            str(jar.resolve()),
            "download",
            package,
            "--output",
            str(downloads.resolve()),
            "--aurora-user-agent",
            os.getenv("AURORA_USER_AGENT", DEFAULT_AURORA_USER_AGENT),
            "--locale",
            os.getenv("GPLAY_LOCALE", "ja-JP"),
        ]
        if candidate and candidate.code:
            command.extend(["--version-code", str(candidate.code)])
            logging.info(
                "🌌 Google Play first: package=%s exact-versionCode=%s (%s)",
                package,
                candidate.code,
                candidate.name,
            )
        else:
            logging.info(
                "🌌 Google Play first: package=%s current Play release%s",
                package,
                f" (wanted versionName {candidate.name})" if candidate else "",
            )

        result = _run(command)
        if result.returncode != 0:
            # gplaydl never prints the anonymous auth token; still keep failure
            # output bounded so provider HTML or unrelated diagnostics cannot
            # flood Actions logs.
            tail = "\n".join((result.stdout or "").splitlines()[-35:])
            raise RuntimeError(f"gplaydl exited non-zero: {tail}")

        package_dir = downloads / package
        apk_files = list(package_dir.glob("*.apk")) if package_dir.is_dir() else []
        if not apk_files:
            apk_files = list(downloads.rglob("*.apk"))
        if not apk_files:
            tail = "\n".join((result.stdout or "").splitlines()[-20:])
            raise IOError(
                f"Google Play produced no APK files for {package}"
                + (f": {tail}" if tail else "")
            )
        return _package_apks(apk_files, package, output_dir)


def download_current(package: str, output_dir: Path | None = None) -> Path:
    """Compatibility wrapper for callers that want the current Play release."""
    return download_candidate(package, None, output_dir)
