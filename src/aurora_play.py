"""Authenticated Google Play APK downloads.

Google Play is the preferred APK origin for every app except packages that are
explicitly GitHub-only (currently AdGuard). Downloads use upstream ``gplaydl``.
When ``GPLAY_EMAIL`` and ``GPLAY_AAS_TOKEN`` are available, CI first starts an
ephemeral self-hosted dispenser on the runner and points gplaydl at it; otherwise
the configured ``GPLAYDL_API_KEY``/dispenser path remains available.

For explicitly versioned patch targets, Android ``versionCode`` is resolved
at runtime before invoking gplaydl and passed through ``-v``. ``any`` remains
the only path that intentionally asks Google Play for the current release.

The upstream gplaydl CLI owns authentication, details, purchase, delivery,
protobuf handling, device-profile selection, and downloads. This wrapper does
not rewrite Play purchase/delivery requests. ``GPLAYDL_DISPENSER_URL`` may point
gplaydl at a compatible self-hosted or future region-aware dispenser without
changing app selection logic.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

from src import apk_identity, local_gplaydl_dispenser, play_version_resolver
from src.versioning import VersionCandidate

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
) -> list[str]:
    """Build one authenticated upstream-gplaydl command without credentials in argv."""
    command = [
        executable,
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

    If a known exact versionCode is temporarily unavailable through a device
    profile, probe the current release once and accept it only when its manifest
    is exactly the requested release.
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
        command = _linked_gplaydl_command(executable, package, downloads, exact_code)

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

        exact_tail = "\n".join((result.stdout or "").splitlines()[-35:])
        if not exact_code or candidate is None:
            raise RuntimeError(f"authenticated gplaydl exited non-zero: {exact_tail}")

        logging.warning(
            "⚠️  Authenticated exact versionCode %s was not downloadable for %s; "
            "probing the current Play release with the same token and requiring "
            "exact manifest identity",
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
                "authenticated gplaydl exact-version attempt failed, and authenticated "
                f"current-release probe also failed. exact: {exact_tail}; current: {current_tail}"
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
            "✅ Authenticated current Play release exactly matches requested candidate: "
            "package=%s versionName=%s versionCode=%s",
            identity.package_name,
            identity.version_name,
            identity.version_code or "unknown",
        )
        return current_input


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

    # No anonymous fallback is permitted. Failure here intentionally bubbles up
    # to the caller, which can then try configured non-Play providers.
    return _download_with_linked_gplaydl(package, play_candidate, output_dir)


def download_current(package: str, output_dir: Path | None = None) -> Path:
    """Compatibility wrapper for callers that want the current Play release."""
    return download_candidate(package, None, output_dir)
