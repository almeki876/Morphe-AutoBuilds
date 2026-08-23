"""Authenticated Google Play APK downloads.

Google Play is the preferred APK origin for every app except packages that are
explicitly GitHub-only (currently AdGuard). Current releases use apkeep 1.0.0's
Google Play implementation first; exact versionCodes use upstream ``gplaydl``.
When ``GPLAY_EMAIL`` and ``GPLAY_AAS_TOKEN`` are available, gplaydl starts an
ephemeral self-hosted dispenser on the runner. apkeep reads the same account
from a short-lived owner-only INI file and never accepts Play terms for users.

For explicitly versioned patch targets, Android ``versionCode`` is resolved
at runtime before invoking gplaydl and passed through ``-v``. ``any`` remains
the only path that intentionally asks Google Play for the current release.

The upstream clients own authentication, details, purchase, delivery, protobuf
handling, device-profile selection, and downloads. This wrapper does not
rewrite Play purchase/delivery requests. Every returned archive is checked
against its Android manifest before it can enter the build pipeline.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

from src import apk_identity, local_gplaydl_dispenser, play_version_resolver
from src.versioning import VersionCandidate

OFFICIAL_GPLAYDL_COMMAND = "gplaydl"
OFFICIAL_APKEEP_COMMAND = "apkeep"
SUPPORTED_APKEEP_VERSION = "1.0.0"
APKEEP_GOOGLE_PLAY_OPTIONS = (
    "device=px_9a,locale=ja_JP,timezone=Asia/Tokyo,split_apk=true"
)

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
    """Return whether CI supplied the required gplaydl 4.x API key."""
    return bool(os.getenv("GPLAYDL_API_KEY", "").strip())


def _require_linked_account() -> None:
    """Refuse Google Play access unless the linked-account token is present."""
    if not _linked_account_configured():
        raise RuntimeError(
            "GPLAYDL_API_KEY is required for Google Play downloads; "
            "anonymous Google Play downloads are disabled"
        )


def _linked_gplaydl_command(
    executable: str,
    package: str,
    downloads: Path,
    version_code: str | None,
    *,
    profile_retry: bool = False,
) -> list[str]:
    """Build one authenticated upstream-gplaydl command without credentials in argv."""
    prefix = (
        [sys.executable, "-m", "src.gplaydl_profile_retry"]
        if profile_retry
        else [executable]
    )
    command = prefix + [
        "download",
        package,
        "-o",
        str(downloads.resolve()),
        "-a",
        os.getenv("GPLAYDL_ARCH", "arm64"),
    ]
    configured_dispenser = os.getenv("GPLAYDL_DISPENSER_URL", "").strip()
    if configured_dispenser:
        command.extend(["--dispenser", configured_dispenser])
    configured_email = (
        os.getenv("GPLAYDL_EMAIL", "").strip()
        or os.getenv("GPLAY_EMAIL", "").strip()
    )
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


def _secret_safe_text(text: str) -> str:
    """Redact repository Play credentials from a child-process diagnostic."""
    safe = text or ""
    for name in (
        "GPLAY_AAS_TOKEN",
        "GPLAY_AUTH_TOKEN",
        "GPLAYDL_API_KEY",
        "GPLAY_EMAIL",
        "GPLAYDL_EMAIL",
    ):
        value = os.getenv(name, "").strip()
        if value:
            safe = safe.replace(value, f"[redacted-{name.lower()}]")
    safe = re.sub(r"aas_et/[A-Za-z0-9_./+=-]+", "[redacted-aas-token]", safe)
    safe = re.sub(r"ya29\.[A-Za-z0-9_./+=-]+", "[redacted-auth-token]", safe)
    return safe


def _write_apkeep_ini(path: Path, email: str, aas_token: str) -> None:
    """Create an apkeep credential file atomically with owner-only access."""
    if any(char in email or char in aas_token for char in "\r\n"):
        raise RuntimeError("Google Play credentials contain an invalid newline")
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(f"[google]\nemail = {email}\naas_token = {aas_token}\n")
    except Exception:
        path.unlink(missing_ok=True)
        raise
    if os.name != "nt" and stat.S_IMODE(path.stat().st_mode) != 0o600:
        path.unlink(missing_ok=True)
        raise RuntimeError("apkeep credential file is not owner-only")


def _download_with_apkeep_google_play(package: str, output_dir: Path) -> Path:
    """Download current Play APKs through apkeep using repository AAS auth.

    This is a second Google Play protocol implementation, not a mirror.  Terms
    of Service are never accepted automatically.
    """
    executable = shutil.which(OFFICIAL_APKEEP_COMMAND)
    if not executable:
        raise FileNotFoundError("apkeep 1.0.0 is required for Google Play fallback")
    env = os.environ.copy()
    for name in (
        "GPLAY_EMAIL",
        "GPLAYDL_EMAIL",
        "GPLAY_AAS_TOKEN",
        "GPLAY_AUTH_TOKEN",
        "GPLAYDL_API_KEY",
    ):
        env.pop(name, None)

    version_result = _run([executable, "--version"], env=env)
    version_output = " ".join((version_result.stdout or "").split())
    if (
        version_result.returncode != 0
        or version_output != f"apkeep {SUPPORTED_APKEEP_VERSION}"
    ):
        raise RuntimeError(
            "apkeep Google Play fallback requires exactly "
            f"{SUPPORTED_APKEEP_VERSION}, found {version_output or 'unknown'}"
        )

    email = os.getenv("GPLAY_EMAIL", "").strip()
    aas_token = os.getenv("GPLAY_AAS_TOKEN", "").strip()
    if not email or not aas_token:
        raise RuntimeError(
            "apkeep Google Play fallback requires GPLAY_EMAIL and GPLAY_AAS_TOKEN"
        )
    if not aas_token.startswith("aas_et/"):
        raise RuntimeError("GPLAY_AAS_TOKEN does not look like an AAS token")

    with tempfile.TemporaryDirectory(prefix="apkeep-google-play-", dir=output_dir) as tmp:
        root = Path(tmp)
        config = root / "apkeep.ini"
        downloads = root / "downloads"
        downloads.mkdir()
        _write_apkeep_ini(config, email, aas_token)

        command = [
            executable,
            "-a",
            package,
            "-d",
            "google-play",
            "-o",
            APKEEP_GOOGLE_PLAY_OPTIONS,
            "-i",
            str(config),
            str(downloads),
        ]
        logging.info(
            "🔐 Downloading current release through apkeep Google Play: package=%s "
            "device=px_9a locale=ja_JP timezone=Asia/Tokyo splits=true",
            package,
        )
        result = _run(command, env=env)
        apk_files = [
            path
            for path in downloads.rglob("*.apk")
            if path.is_file() and path.stat().st_size > 0
        ]
        if result.returncode != 0 or not apk_files:
            tail = "\n".join((result.stdout or "").splitlines()[-30:])
            detail = _secret_safe_text(tail).strip() or "no APK output"
            raise RuntimeError(
                f"apkeep Google Play fallback produced no usable APKs: {detail}"
            )
        packaged = _package_apks(apk_files, package, output_dir)
        try:
            identity = apk_identity.validate_identity(packaged, package, None)
        except Exception:
            packaged.unlink(missing_ok=True)
            raise
        logging.info(
            "✅ apkeep Google Play manifest verified: package=%s "
            "versionName=%s versionCode=%s",
            identity.package_name,
            identity.version_name or "unknown",
            identity.version_code or "unknown",
        )
        return packaged


def _download_with_linked_gplaydl(
    package: str,
    candidate: VersionCandidate | None,
    output_dir: Path,
) -> Path:
    """Download through upstream gplaydl 4.x using a linked account.

    An explicit patch version always reaches this function with a resolved
    versionCode and is sent to gplaydl through ``-v``. Only ``candidate=None``
    (patch compatibility ``any``) intentionally requests the current release.

    If AAS credentials are present, ``ensure_running`` replaces the hosted
    dispenser credentials in this process with an ephemeral localhost dispenser
    before gplaydl is started.

    The guarded wrapper adds the one missing upstream fallback: when delivery
    says the account has not acquired the app, retry all priority profiles with
    a fresh Google check-in/token.  All download and digest verification stays
    inside the pinned upstream CLI.
    """
    local_gplaydl_dispenser.ensure_running()
    _require_linked_account()

    executable = shutil.which(OFFICIAL_GPLAYDL_COMMAND)
    if not executable:
        raise FileNotFoundError(
            "GPLAYDL_API_KEY is configured but the upstream gplaydl CLI is not installed"
        )

    with tempfile.TemporaryDirectory(prefix="linked-google-play-", dir=output_dir) as tmp:
        downloads = Path(tmp) / "downloads"
        downloads.mkdir()
        exact_code = str(candidate.code) if candidate and candidate.code else None
        command = _linked_gplaydl_command(
            executable,
            package,
            downloads,
            exact_code,
            profile_retry=True,
        )

        logging.info(
            "🔐 Authenticated Google Play first: package=%s%s%s",
            package,
            f" exact-versionCode={candidate.code} ({candidate.name})"
            if exact_code and candidate
            else " current release",
            " custom-dispenser" if os.getenv("GPLAYDL_DISPENSER_URL", "").strip() else "",
        )
        result = _run(command)
        if result.returncode == 0:
            return _collect_linked_download(downloads, package, output_dir, result)

        tail = "\n".join((result.stdout or "").splitlines()[-35:])
        raise RuntimeError(f"authenticated gplaydl exited non-zero: {tail}")


def download_candidate(
    package: str,
    candidate: VersionCandidate | None,
    output_dir: Path | None = None,
) -> Path:
    """Download a Play release using only the linked-account token."""
    if not google_play_enabled(package):
        raise GooglePlayDisabled(
            f"Google Play is disabled by repository policy for {package}; use GitHub"
        )

    # Universal policy for every Play-enabled app:
    #   any -> current release
    #   explicit version -> dynamically resolve exact Android versionCode, then -v
    play_candidate = play_version_resolver.resolve_candidate(package, candidate)

    output_dir = output_dir or Path(".")
    output_dir.mkdir(parents=True, exist_ok=True)

    if play_candidate is None:
        # Current releases use apkeep/rs-google-play first.  Its FDFE wire is
        # independent of gplaydl 4.2.1 and current (query + empty purchase POST,
        # current DFE headers), while still downloading only from Google Play.
        try:
            return _download_with_apkeep_google_play(package, output_dir)
        except Exception as apkeep_error:
            logging.warning(
                "⚠️  Primary apkeep Google Play current-release download failed "
                "for %s: %s; trying authenticated gplaydl with fresh-profile retry",
                package,
                _secret_safe_text(str(apkeep_error)),
            )
            try:
                return _download_with_linked_gplaydl(package, None, output_dir)
            except Exception as gplaydl_error:
                raise RuntimeError(
                    "both official Google Play download paths failed. apkeep: "
                    f"{_secret_safe_text(str(apkeep_error))}; gplaydl: "
                    f"{_secret_safe_text(str(gplaydl_error))}"
                ) from gplaydl_error

    # Exact versionCodes remain gplaydl-first because apkeep exposes only the
    # current Play release.  If the exact attempt fails, apkeep may still be
    # used only when the current manifest is exactly the requested candidate.
    try:
        return _download_with_linked_gplaydl(package, play_candidate, output_dir)
    except Exception as gplaydl_error:
        logging.warning(
            "⚠️  Authenticated exact Google Play download failed for %s; "
            "probing current Play through apkeep and requiring exact identity",
            package,
        )
        try:
            current_input = _download_with_apkeep_google_play(package, output_dir)
        except Exception as apkeep_error:
            raise RuntimeError(
                "authenticated exact-version gplaydl and current-release apkeep "
                "Google Play paths both failed. gplaydl: "
                f"{_secret_safe_text(str(gplaydl_error))}; apkeep: "
                f"{_secret_safe_text(str(apkeep_error))}"
            ) from apkeep_error

        try:
            identity = apk_identity.validate_identity(
                current_input,
                package,
                play_candidate,
            )
        except Exception:
            current_input.unlink(missing_ok=True)
            raise
        logging.info(
            "✅ apkeep current Play release exactly matches requested candidate: "
            "package=%s versionName=%s versionCode=%s",
            identity.package_name,
            identity.version_name,
            identity.version_code or "unknown",
        )
        return current_input


def download_current(package: str, output_dir: Path | None = None) -> Path:
    """Compatibility wrapper for callers that want the current Play release."""
    return download_candidate(package, None, output_dir)
