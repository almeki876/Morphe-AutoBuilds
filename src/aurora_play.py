"""Google Play APK downloads with authenticated and anonymous fallbacks.

When ``GPLAYDL_API_KEY`` is available, prefer the pinned upstream gplaydl CLI
and the user's linked Google account. If that path is unavailable or fails, the
repo-local Aurora-style downloader remains as a fallback. Every returned APK is
still validated before it can be cached or patched.
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

from src import apk_identity
from src.versioning import VersionCandidate

GPLAYDL_PROJECT = Path("tools/gplaydl/pom.xml")
GPLAYDL_SOURCE_ROOT = Path("tools/gplaydl/src")
GPLAYDL_JAR = Path("tools/gplaydl/target/gplaydl-1.0-SNAPSHOT-all.jar")
GPLAYDL_FINGERPRINT = Path("tools/gplaydl/target/gplaydl-source.sha256")
DEFAULT_AURORA_USER_AGENT = "com.aurora.store-4.8.4-76"
OFFICIAL_GPLAYDL_COMMAND = "gplaydl"

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


def _restore_checked_in_gplaydl_sources() -> None:
    """Undo Actions cache contamination of tracked gplaydl source files."""
    if os.getenv("GITHUB_ACTIONS", "").casefold() != "true":
        return

    result = _run(
        [
            "git",
            "restore",
            "--source=HEAD",
            "--worktree",
            "--",
            str(GPLAYDL_PROJECT),
            str(GPLAYDL_SOURCE_ROOT),
        ]
    )
    if result.returncode != 0:
        tail = "\n".join((result.stdout or "").splitlines()[-20:])
        raise RuntimeError(
            "could not restore checked-in gplaydl sources after tool cache restore"
            + (f": {tail}" if tail else "")
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
    _restore_checked_in_gplaydl_sources()
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


def _linked_account_configured() -> bool:
    """Return whether CI supplied a gplaydl 4.x linked-account API key."""
    return bool(os.getenv("GPLAYDL_API_KEY", "").strip())


def _linked_gplaydl_command(
    executable: str,
    package: str,
    downloads: Path,
    version_code: str | None,
) -> list[str]:
    """Build one upstream gplaydl command without putting credentials in argv."""
    command = [
        executable,
        "download",
        package,
        "-o",
        str(downloads.resolve()),
        "-a",
        os.getenv("GPLAYDL_ARCH", "arm64"),
    ]
    configured_email = os.getenv("GPLAYDL_EMAIL", "").strip()
    if configured_email:
        command.extend(["--email", configured_email])
    if version_code:
        command.extend(["-v", version_code])
    return command


def _collect_linked_download(
    downloads: Path,
    package: str,
    output_dir: Path,
    result: subprocess.CompletedProcess[str],
) -> Path:
    """Package the APK files from one successful linked-account attempt."""
    apk_files = list(downloads.rglob("*.apk"))
    if not apk_files:
        tail = "\n".join((result.stdout or "").splitlines()[-20:])
        raise IOError(
            f"linked gplaydl produced no APK files for {package}"
            + (f": {tail}" if tail else "")
        )
    return _package_apks(apk_files, package, output_dir)


def _download_with_linked_gplaydl(
    package: str,
    candidate: VersionCandidate | None,
    output_dir: Path,
) -> Path:
    """Download through upstream gplaydl 4.x using ``GPLAYDL_API_KEY``.

    When an exact versionCode lookup is unavailable, gplaydl can still serve the
    same release through its normal current-release path. Probe that path once,
    but only accept it after manifest identity matches the original exact
    candidate. A different current release is deleted and treated as failure so
    the repo-local exact-version fallback can still run.
    """
    executable = shutil.which(OFFICIAL_GPLAYDL_COMMAND)
    if not executable:
        raise FileNotFoundError(
            "GPLAYDL_API_KEY is configured but the upstream gplaydl CLI is not installed"
        )

    with tempfile.TemporaryDirectory(prefix="linked-google-play-", dir=output_dir) as tmp:
        downloads = Path(tmp) / "downloads"
        downloads.mkdir()
        exact_code = str(candidate.code) if candidate and candidate.code else None
        command = _linked_gplaydl_command(executable, package, downloads, exact_code)

        logging.info(
            "🔐 Google Play linked-account first: package=%s%s",
            package,
            f" exact-versionCode={candidate.code} ({candidate.name})"
            if exact_code and candidate
            else " current release",
        )
        result = _run(command)
        if result.returncode == 0:
            return _collect_linked_download(downloads, package, output_dir, result)

        exact_tail = "\n".join((result.stdout or "").splitlines()[-35:])
        if not exact_code or candidate is None:
            raise RuntimeError(f"linked gplaydl exited non-zero: {exact_tail}")

        logging.warning(
            "⚠️  Linked-account exact versionCode %s was not downloadable for %s; "
            "probing the current Play release and requiring exact manifest identity",
            exact_code,
            package,
        )
        shutil.rmtree(downloads, ignore_errors=True)
        downloads.mkdir()
        current_command = _linked_gplaydl_command(executable, package, downloads, None)
        current_result = _run(current_command)
        if current_result.returncode != 0:
            current_tail = "\n".join((current_result.stdout or "").splitlines()[-35:])
            raise RuntimeError(
                "linked gplaydl exact-version attempt failed, and current-release probe "
                f"also failed. exact: {exact_tail}; current: {current_tail}"
            )

        current_input = _collect_linked_download(
            downloads, package, output_dir, current_result
        )
        try:
            identity = apk_identity.validate_identity(current_input, package, candidate)
        except Exception:
            current_input.unlink(missing_ok=True)
            raise

        logging.info(
            "✅ Current Play release exactly matches requested candidate: "
            "package=%s versionName=%s versionCode=%s",
            identity.package_name,
            identity.version_name,
            identity.version_code or "unknown",
        )
        return current_input


def _download_with_repo_local_gplaydl(
    package: str,
    candidate: VersionCandidate | None,
    output_dir: Path,
) -> Path:
    """Use the legacy repo-local downloader as an independent fallback."""
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
                "🌌 Repo-local Google Play fallback: package=%s exact-versionCode=%s (%s)",
                package,
                candidate.code,
                candidate.name,
            )
        else:
            logging.info(
                "🌌 Repo-local Google Play fallback: package=%s current Play release%s",
                package,
                f" (wanted versionName {candidate.name})" if candidate else "",
            )

        # GPLAYDL_API_KEY belongs to upstream gplaydl 4.x. Never forward it to
        # the legacy anonymous dispenser as an X-Api-Key credential.
        legacy_env = os.environ.copy()
        legacy_env.pop("GPLAYDL_API_KEY", None)
        result = _run(command, env=legacy_env)
        if result.returncode != 0:
            tail = "\n".join((result.stdout or "").splitlines()[-35:])
            raise RuntimeError(f"repo-local gplaydl exited non-zero: {tail}")

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


def download_candidate(
    package: str,
    candidate: VersionCandidate | None,
    output_dir: Path | None = None,
) -> Path:
    """Download a Play release, preferring linked-account authentication."""
    if not google_play_enabled(package):
        raise GooglePlayDisabled(
            f"Google Play is disabled by repository policy for {package}; use GitHub"
        )

    output_dir = output_dir or Path(".")
    output_dir.mkdir(parents=True, exist_ok=True)

    if _linked_account_configured():
        try:
            return _download_with_linked_gplaydl(package, candidate, output_dir)
        except Exception as error:
            logging.warning(
                "⚠️  Linked-account Google Play failed for %s: %s: %s; "
                "trying repo-local Google Play fallback",
                package,
                type(error).__name__,
                error,
            )

    return _download_with_repo_local_gplaydl(package, candidate, output_dir)


def download_current(package: str, output_dir: Path | None = None) -> Path:
    """Compatibility wrapper for callers that want the current Play release."""
    return download_candidate(package, None, output_dir)
