"""Authenticated Google Play APK downloads.

Google Play is the preferred APK origin for every app except packages that are
explicitly GitHub-only (currently AdGuard). Current releases use playfetch's
current Google Play implementation first, with apkeep 1.0.0 and upstream
``gplaydl`` as independent fallbacks; exact versionCodes use upstream gplaydl.
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

import hashlib
import json
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
OFFICIAL_PLAYFETCH_COMMAND = "playfetch"
SUPPORTED_APKEEP_VERSION = "1.0.0"
SUPPORTED_PLAYFETCH_VERSION = "v0.9.1"
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


def _write_playfetch_credentials(path: Path, email: str, aas_token: str) -> None:
    """Create a playfetch v2 account store without exposing secrets in argv."""
    if any(char in email or char in aas_token for char in "\r\n"):
        raise RuntimeError("Google Play credentials contain an invalid newline")
    payload = {
        "version": 2,
        "default": "ci",
        "accounts": [
            {
                "name": "ci",
                "email": email,
                "aas_token": aas_token,
                "region": "JP",
                "added_at": "0001-01-01T00:00:00Z",
            }
        ],
    }
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=True, separators=(",", ":"))
            handle.write("\n")
    except Exception:
        path.unlink(missing_ok=True)
        raise
    if os.name != "nt" and stat.S_IMODE(path.stat().st_mode) != 0o600:
        path.unlink(missing_ok=True)
        raise RuntimeError("playfetch credential file is not owner-only")


def _verified_playfetch_files(manifest_path: Path, package: str) -> list[Path]:
    """Validate playfetch provenance and re-hash every Play-verified APK."""
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"playfetch produced an invalid manifest: {error}") from error

    if not isinstance(manifest, dict):
        raise RuntimeError("playfetch manifest root must be a JSON object")
    source = manifest.get("source") or {}
    app = manifest.get("app") or {}
    records = manifest.get("files")
    if not isinstance(source, dict) or source.get("kind") != "google-play":
        raise RuntimeError("playfetch manifest does not identify Google Play as its source")
    if not isinstance(app, dict) or app.get("package") != package:
        raise RuntimeError(
            "playfetch manifest package mismatch: "
            f"expected {package}, found "
            f"{app.get('package') if isinstance(app, dict) else 'unknown'}"
        )
    if not isinstance(records, list) or not records:
        raise RuntimeError("playfetch manifest contains no downloaded files")

    root = manifest_path.parent.resolve()
    apk_files: list[Path] = []
    for record in records:
        name = record.get("name") if isinstance(record, dict) else None
        if not name or not name.endswith(".apk") or not record.get("verified"):
            raise RuntimeError(
                "playfetch did not verify every APK against a Play-published digest"
            )
        path = (root / name).resolve()
        try:
            path.relative_to(root)
        except ValueError as error:
            raise RuntimeError("playfetch manifest contains an unsafe file path") from error
        if not path.is_file() or path.stat().st_size != record.get("size"):
            raise RuntimeError(f"playfetch output size mismatch for {name}")
        local_hashes = record.get("local") or {}
        if not isinstance(local_hashes, dict):
            raise RuntimeError(f"playfetch manifest has invalid local hashes for {name}")
        sha256_digest = hashlib.sha256()
        sha1_digest = hashlib.sha1(usedforsecurity=False)
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                sha256_digest.update(chunk)
                sha1_digest.update(chunk)
        actual_sha256 = sha256_digest.hexdigest()
        actual_sha1 = sha1_digest.hexdigest()
        local_sha256 = (local_hashes.get("sha256") or "").lower()
        play_sha256 = (record.get("play_sha256") or "").lower()
        play_sha1 = (record.get("play_sha1") or "").lower()
        if not local_sha256 or actual_sha256 != local_sha256:
            raise RuntimeError(f"playfetch output SHA256 mismatch for {name}")
        if not play_sha256 and not play_sha1:
            raise RuntimeError(f"Google Play published no digest for {name}")
        if play_sha256 and actual_sha256 != play_sha256:
            raise RuntimeError(f"Google Play SHA256 mismatch for {name}")
        if play_sha1 and actual_sha1 != play_sha1:
            raise RuntimeError(f"Google Play SHA1 mismatch for {name}")
        apk_files.append(path)
    return apk_files


def _download_with_playfetch_google_play(package: str, output_dir: Path) -> Path:
    """Download a current release with strict Google Play hash provenance."""
    executable = shutil.which(OFFICIAL_PLAYFETCH_COMMAND)
    if not executable:
        raise FileNotFoundError(
            f"playfetch {SUPPORTED_PLAYFETCH_VERSION} is required for Google Play"
        )

    email = os.getenv("GPLAY_EMAIL", "").strip()
    aas_token = os.getenv("GPLAY_AAS_TOKEN", "").strip()
    if not email or not aas_token:
        raise RuntimeError("playfetch requires GPLAY_EMAIL and GPLAY_AAS_TOKEN")
    if not aas_token.startswith("aas_et/"):
        raise RuntimeError("GPLAY_AAS_TOKEN does not look like an AAS token")

    env = os.environ.copy()
    for name in (
        "GPLAY_EMAIL",
        "GPLAYDL_EMAIL",
        "GPLAY_AAS_TOKEN",
        "GPLAY_AUTH_TOKEN",
        "GPLAYDL_API_KEY",
        "PLAYFETCH_CREDENTIALS",
    ):
        env.pop(name, None)

    version_result = _run([executable, "version"], env=env)
    version_output = " ".join((version_result.stdout or "").split())
    if (
        version_result.returncode != 0
        or version_output != f"playfetch {SUPPORTED_PLAYFETCH_VERSION}"
    ):
        raise RuntimeError(
            "Google Play download requires exactly playfetch "
            f"{SUPPORTED_PLAYFETCH_VERSION}, found {version_output or 'unknown'}"
        )

    with tempfile.TemporaryDirectory(prefix="playfetch-google-play-", dir=output_dir) as tmp:
        root = Path(tmp)
        credentials = root / "credentials.json"
        session = root / "session.json"
        downloads = root / "downloads"
        downloads.mkdir()
        _write_playfetch_credentials(credentials, email, aas_token)

        command = [
            executable,
            "pull",
            package,
            "-out",
            str(downloads),
            "-mode",
            "split",
            "-profile",
            "px_9a",
            "-locale",
            "ja_JP",
            "-tz",
            "Asia/Tokyo",
            "-session",
            str(session),
            "-refresh",
            "-credentials",
            str(credentials),
            "-account",
            "ci",
        ]
        logging.info(
            "🔐 Downloading current release through playfetch Google Play: "
            "package=%s device=px_9a locale=ja_JP timezone=Asia/Tokyo",
            package,
        )
        result = _run(command, env=env)
        if result.returncode != 0:
            tail = "\n".join((result.stdout or "").splitlines()[-40:])
            raise RuntimeError(
                "playfetch Google Play download failed: "
                f"{_secret_safe_text(tail).strip() or 'no diagnostic output'}"
            )

        apk_files = _verified_playfetch_files(downloads / "manifest.json", package)
        packaged = _package_apks(apk_files, package, output_dir)
        try:
            identity = apk_identity.validate_identity(packaged, package, None)
        except Exception:
            packaged.unlink(missing_ok=True)
            raise
        logging.info(
            "✅ playfetch Google Play hashes and manifest verified: package=%s "
            "versionName=%s versionCode=%s files=%d",
            identity.package_name,
            identity.version_name or "unknown",
            identity.version_code or "unknown",
            len(apk_files),
        )
        return packaged


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
        # playfetch has current DFE headers, strict non-zero failures, and
        # verifies every delivered file against hashes published by Play.
        try:
            return _download_with_playfetch_google_play(package, output_dir)
        except Exception as playfetch_error:
            logging.warning(
                "⚠️  Primary playfetch Google Play download failed for %s: %s; "
                "trying independent apkeep Google Play implementation",
                package,
                _secret_safe_text(str(playfetch_error)),
            )
            try:
                return _download_with_apkeep_google_play(package, output_dir)
            except Exception as apkeep_error:
                logging.warning(
                    "⚠️  apkeep Google Play download failed for %s: %s; trying "
                    "authenticated gplaydl with fresh-profile retry",
                    package,
                    _secret_safe_text(str(apkeep_error)),
                )
                try:
                    return _download_with_linked_gplaydl(package, None, output_dir)
                except Exception as gplaydl_error:
                    raise RuntimeError(
                        "all three Google Play download paths failed. playfetch: "
                        f"{_secret_safe_text(str(playfetch_error))}; apkeep: "
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
