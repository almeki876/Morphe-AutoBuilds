"""Google Play APK downloads through the pinned anonymous gplaydl 2.x client.

The repository prefers Google Play bodies whenever possible. gplaydl 2.x obtains
short-lived anonymous Play credentials from Aurora Store's public token
dispenser, purchases an exact versionCode when supplied, and downloads the
complete Play file set (base plus split APKs). The caller validates
package/version identity before accepting or caching the result.
"""

from __future__ import annotations

import importlib.util
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

from src.versioning import VersionCandidate

GPLAYDL_MODULE = "gplaydl"
DEFAULT_GPLAY_ARCH = "arm64"
DEFAULT_AURORA_DISPENSER = "https://auroraoss.com/api/auth"
AURORA_USER_AGENTS = (
    "com.aurora.store-4.8.4-76",
    "com.aurora.store-4.8.3-75",
    "com.aurora.store-4.6.1-70",
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


def _ensure_downloader() -> list[str]:
    """Return the pinned Python gplaydl CLI invocation."""
    if importlib.util.find_spec(GPLAYDL_MODULE) is None:
        raise FileNotFoundError(
            "gplaydl is required for anonymous Google Play downloads; "
            "install requirements.txt"
        )
    return [sys.executable, "-m", GPLAYDL_MODULE]


def _refresh_anonymous_auth(arch: str) -> None:
    """Mint and cache an anonymous Aurora token using current Store UAs.

    gplaydl 2.1.7 hard-codes an older Aurora Store user-agent. Aurora's token
    dispenser can reject stale clients, so try the current stable identifiers
    first while still using gplaydl's checked-in device profiles and cache
    format. No Google account or repository secret is used.
    """
    import httpx
    from gplaydl.profiles import FALLBACK_PROFILE, get_priority_profiles

    profiles = get_priority_profiles(arch) or [("fallback", FALLBACK_PROFILE)]
    dispenser = os.getenv("AURORA_DISPENSER_URL", DEFAULT_AURORA_DISPENSER)
    attempts: list[str] = []

    for user_agent in AURORA_USER_AGENTS:
        headers = {
            "User-Agent": user_agent,
            "Content-Type": "application/json",
        }
        for profile_name, profile in profiles:
            try:
                response = httpx.post(
                    dispenser,
                    json=profile,
                    headers=headers,
                    timeout=30,
                    follow_redirects=True,
                )
            except Exception as exc:
                attempts.append(f"{user_agent}/{profile_name}: {type(exc).__name__}")
                continue

            attempts.append(f"{user_agent}/{profile_name}: HTTP {response.status_code}")
            if response.status_code != 200:
                continue
            try:
                data = response.json()
            except Exception:
                continue
            if not data.get("authToken"):
                continue

            config_dir = Path.home() / ".config" / "gplaydl"
            config_dir.mkdir(parents=True, exist_ok=True)
            data["_cached_at"] = time.time()
            auth_path = config_dir / f"auth-{arch}.json"
            auth_path.write_text(json.dumps(data))
            logging.info(
                "🌌 Anonymous Google Play auth succeeded with Aurora client %s",
                user_agent,
            )
            return

    summary = "; ".join(attempts[-12:])
    raise RuntimeError(f"Aurora anonymous auth rejected all profiles: {summary}")


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
    """Download a Play release, requesting ``candidate.code`` when known."""
    if not google_play_enabled(package):
        raise GooglePlayDisabled(
            f"Google Play is disabled by repository policy for {package}; use GitHub"
        )

    output_dir = output_dir or Path(".")
    output_dir.mkdir(parents=True, exist_ok=True)
    cli = _ensure_downloader()
    arch = os.getenv("GPLAY_ARCH", DEFAULT_GPLAY_ARCH)

    env = os.environ.copy()
    env.pop("GPLAYDL_API_KEY", None)
    env["GPLAYDL_NO_BANNER"] = "1"

    _refresh_anonymous_auth(arch)

    with tempfile.TemporaryDirectory(prefix="google-play-", dir=output_dir) as tmp:
        downloads = Path(tmp) / "downloads"
        downloads.mkdir()
        command = [
            *cli,
            "download",
            package,
            "-o",
            str(downloads.resolve()),
            "-a",
            arch,
        ]

        if candidate and candidate.code:
            command.extend(["-v", str(candidate.code)])
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

        result = _run(command, env=env)
        if result.returncode != 0:
            tail = "\n".join((result.stdout or "").splitlines()[-35:])
            raise RuntimeError(f"gplaydl exited non-zero: {tail}")

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
