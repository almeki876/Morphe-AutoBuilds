"""Authenticated Google Play downloads with bounded fallback latency.

Google Play remains the preferred APK origin for every app except packages that
are explicitly GitHub-only. Current releases try pinned gplaydl first using the
already configured account/dispenser, then playfetch, then apkeep. A final
fresh-device gplaydl retry remains available as the expensive safety net for
region/device restricted apps. Exact versionCodes use gplaydl first and only
accept a current-release fallback when its manifest exactly matches the
requested candidate.

The module deliberately owns only orchestration, credential hygiene, result
verification, deterministic split packaging, and time budgets. The pinned
upstream clients continue to own Play protocol details, purchase/delivery, and
APK downloads.
"""

from __future__ import annotations

import contextvars
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
import time
from pathlib import Path

from src import archive_stability, apk_identity, local_gplaydl_dispenser, play_version_resolver
from src.versioning import VersionCandidate

OFFICIAL_GPLAYDL_COMMAND = "gplaydl"
OFFICIAL_APKEEP_COMMAND = "apkeep"
OFFICIAL_PLAYFETCH_COMMAND = "playfetch"
SUPPORTED_APKEEP_VERSION = "1.0.0"
SUPPORTED_PLAYFETCH_VERSION = "v0.9.1"
APKEEP_GOOGLE_PLAY_OPTIONS = (
    "device=px_9a,locale=ja_JP,timezone=Asia/Tokyo,split_apk=true"
)

GITHUB_ONLY_PACKAGES = frozenset({"com.adguard.android"})
_TOTAL_BUDGET_SECONDS = 120.0
_COMMAND_CAPS = {
    "version": 8.0,
    "playfetch": 35.0,
    "apkeep": 20.0,
    "gplaydl": 35.0,
    "generic": 45.0,
}
_play_deadline: contextvars.ContextVar[float | None] = contextvars.ContextVar(
    "google_play_deadline", default=None
)


class GooglePlayDisabled(RuntimeError):
    """Raised when repository policy forbids Google Play for an app."""


class GooglePlayTimeout(RuntimeError):
    """Raised when one Play path or the whole Play preference budget expires."""


def google_play_enabled(package: str) -> bool:
    return package not in GITHUB_ONLY_PACKAGES


def _env_seconds(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    if raw:
        try:
            return max(1.0, float(raw))
        except ValueError:
            logging.warning("Ignoring invalid %s=%r", name, raw)
    return default


def _command_kind(command: list[str]) -> str:
    rendered = " ".join(command)
    if command and command[-1] in {"version", "--version"}:
        return "version"
    if "playfetch" in rendered:
        return "playfetch"
    if "apkeep" in rendered:
        return "apkeep"
    if "gplaydl" in rendered or "src.gplaydl_profile_retry" in rendered:
        return "gplaydl"
    return "generic"


def _command_timeout(command: list[str]) -> float:
    kind = _command_kind(command)
    env_name = {
        "version": "GPLAY_VERSION_TIMEOUT_SECONDS",
        "playfetch": "GPLAY_PLAYFETCH_TIMEOUT_SECONDS",
        "apkeep": "GPLAY_APKEEP_TIMEOUT_SECONDS",
        "gplaydl": "GPLAY_GPLAYDL_TIMEOUT_SECONDS",
        "generic": "GPLAY_COMMAND_TIMEOUT_SECONDS",
    }[kind]
    cap = _env_seconds(env_name, _COMMAND_CAPS[kind])
    deadline = _play_deadline.get()
    if deadline is None:
        return cap
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise GooglePlayTimeout("Google Play preference budget exhausted; using the next provider")
    return max(1.0, min(cap, remaining))


def _remaining_budget() -> float | None:
    deadline = _play_deadline.get()
    if deadline is None:
        return None
    return max(0.0, deadline - time.monotonic())


def _run(
    command: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run one upstream client under the shared Play latency budget."""
    timeout = _command_timeout(command)
    try:
        return subprocess.run(
            command,
            cwd=cwd,
            env=env,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as error:
        kind = _command_kind(command)
        raise GooglePlayTimeout(
            f"{kind} Google Play attempt exceeded {timeout:.0f}s"
        ) from error


def _package_apks(apk_files: list[Path], package: str, output_dir: Path) -> Path:
    """Return one patcher-compatible input containing all Play-delivered APKs.

    Split containers are deterministic: identical APK payloads create identical
    outer bytes and SHA-256 hashes across runners. This is important for both
    the durable APK cache and VirusTotal hash lookup reuse.
    """
    if not apk_files:
        raise IOError(f"Google Play produced no APK files for {package}")
    if len(apk_files) == 1:
        target = output_dir / f"{package}-google-play.apk"
        shutil.copy2(apk_files[0], target)
        return target

    used: set[str] = set()
    entries: list[tuple[str, Path]] = []
    for index, source in enumerate(sorted(apk_files, key=lambda item: item.name)):
        name = source.name
        if name in used:
            name = f"split-{index}-{name}"
        used.add(name)
        entries.append((name, source))
    return archive_stability.write_files(
        output_dir / f"{package}-google-play.apks",
        entries,
    )


def _linked_account_configured() -> bool:
    return bool(os.getenv("GPLAYDL_API_KEY", "").strip())


def _require_linked_account() -> None:
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
    apk_files = [
        path
        for path in downloads.rglob("*.apk")
        if path.is_file() and path.stat().st_size > 0
    ]
    if not apk_files:
        tail = "\n".join((result.stdout or "").splitlines()[-20:])
        raise IOError(
            f"linked gplaydl produced no APK files for {package}"
            + (f": {tail}" if tail else "")
        )
    return _package_apks(apk_files, package, output_dir)


def _secret_safe_text(text: str) -> str:
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
    safe = re.sub(r"(?i)(email=)[^&\s\"']+", r"\1[redacted-email]", safe)
    return safe


def _write_apkeep_ini(path: Path, email: str, aas_token: str) -> None:
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
            f"expected {package}, found {app.get('package') if isinstance(app, dict) else 'unknown'}"
        )
    if not isinstance(records, list) or not records:
        raise RuntimeError("playfetch manifest contains no downloaded files")

    root = manifest_path.parent.resolve()
    apk_files: list[Path] = []
    for record in records:
        name = record.get("name") if isinstance(record, dict) else None
        if not name or not name.endswith(".apk") or not record.get("verified"):
            raise RuntimeError("playfetch did not verify every APK against a Play-published digest")
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
    executable = shutil.which(OFFICIAL_PLAYFETCH_COMMAND)
    if not executable:
        raise FileNotFoundError(f"playfetch {SUPPORTED_PLAYFETCH_VERSION} is required for Google Play")
    email = os.getenv("GPLAY_EMAIL", "").strip()
    aas_token = os.getenv("GPLAY_AAS_TOKEN", "").strip()
    if not email or not aas_token:
        raise RuntimeError("playfetch requires GPLAY_EMAIL and GPLAY_AAS_TOKEN")
    if not aas_token.startswith("aas_et/"):
        raise RuntimeError("GPLAY_AAS_TOKEN does not look like an AAS token")

    env = os.environ.copy()
    for name in (
        "GPLAY_EMAIL", "GPLAYDL_EMAIL", "GPLAY_AAS_TOKEN", "GPLAY_AUTH_TOKEN",
        "GPLAYDL_API_KEY", "PLAYFETCH_CREDENTIALS",
    ):
        env.pop(name, None)
    version_result = _run([executable, "version"], env=env)
    version_output = " ".join((version_result.stdout or "").split())
    if version_result.returncode != 0 or version_output != f"playfetch {SUPPORTED_PLAYFETCH_VERSION}":
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
            executable, "pull", package, "-out", str(downloads), "-mode", "split",
            "-profile", "px_9a", "-locale", "ja_JP", "-tz", "Asia/Tokyo",
            "-session", str(session), "-refresh", "-credentials", str(credentials),
            "-account", "ci",
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
    executable = shutil.which(OFFICIAL_APKEEP_COMMAND)
    if not executable:
        raise FileNotFoundError("apkeep 1.0.0 is required for Google Play fallback")
    env = os.environ.copy()
    for name in (
        "GPLAY_EMAIL", "GPLAYDL_EMAIL", "GPLAY_AAS_TOKEN", "GPLAY_AUTH_TOKEN", "GPLAYDL_API_KEY",
    ):
        env.pop(name, None)
    version_result = _run([executable, "--version"], env=env)
    version_output = " ".join((version_result.stdout or "").split())
    if version_result.returncode != 0 or version_output != f"apkeep {SUPPORTED_APKEEP_VERSION}":
        raise RuntimeError(
            "apkeep Google Play fallback requires exactly "
            f"{SUPPORTED_APKEEP_VERSION}, found {version_output or 'unknown'}"
        )
    email = os.getenv("GPLAY_EMAIL", "").strip()
    aas_token = os.getenv("GPLAY_AAS_TOKEN", "").strip()
    if not email or not aas_token:
        raise RuntimeError("apkeep Google Play fallback requires GPLAY_EMAIL and GPLAY_AAS_TOKEN")
    if not aas_token.startswith("aas_et/"):
        raise RuntimeError("GPLAY_AAS_TOKEN does not look like an AAS token")

    with tempfile.TemporaryDirectory(prefix="apkeep-google-play-", dir=output_dir) as tmp:
        root = Path(tmp)
        config = root / "apkeep.ini"
        downloads = root / "downloads"
        downloads.mkdir()
        _write_apkeep_ini(config, email, aas_token)
        command = [
            executable, "-a", package, "-d", "google-play", "-o",
            APKEEP_GOOGLE_PLAY_OPTIONS, "-i", str(config), str(downloads),
        ]
        logging.info(
            "🔐 Downloading current release through apkeep Google Play: package=%s "
            "device=px_9a locale=ja_JP timezone=Asia/Tokyo splits=true",
            package,
        )
        result = _run(command, env=env)
        apk_files = [
            path for path in downloads.rglob("*.apk")
            if path.is_file() and path.stat().st_size > 0
        ]
        if result.returncode != 0 or not apk_files:
            tail = "\n".join((result.stdout or "").splitlines()[-30:])
            detail = _secret_safe_text(tail).strip() or "no APK output"
            raise RuntimeError(f"apkeep Google Play fallback produced no usable APKs: {detail}")
        packaged = _package_apks(apk_files, package, output_dir)
        try:
            identity = apk_identity.validate_identity(packaged, package, None)
        except Exception:
            packaged.unlink(missing_ok=True)
            raise
        logging.info(
            "✅ apkeep Google Play manifest verified: package=%s versionName=%s versionCode=%s",
            identity.package_name,
            identity.version_name or "unknown",
            identity.version_code or "unknown",
        )
        return packaged


def _download_with_fast_gplaydl(
    package: str,
    candidate: VersionCandidate | None,
    output_dir: Path,
) -> Path:
    """Try the configured gplaydl account without bootstrapping local services."""
    _require_linked_account()
    executable = shutil.which(OFFICIAL_GPLAYDL_COMMAND)
    if not executable:
        raise FileNotFoundError(
            "GPLAYDL_API_KEY is configured but the upstream gplaydl CLI is not installed"
        )
    with tempfile.TemporaryDirectory(prefix="fast-gplaydl-", dir=output_dir) as tmp:
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
            "🔐 Trying fast gplaydl Google Play path: package=%s%s",
            package,
            f" exact-versionCode={candidate.code} ({candidate.name})"
            if exact_code and candidate
            else " current release",
        )
        result = _run(command)
        if result.returncode == 0:
            return _collect_linked_download(downloads, package, output_dir, result)
        tail = "\n".join((result.stdout or "").splitlines()[-20:])
        raise RuntimeError(
            "fast gplaydl exited non-zero: "
            f"{_secret_safe_text(tail).strip() or 'no diagnostic output'}"
        )


def _download_with_linked_gplaydl(
    package: str,
    candidate: VersionCandidate | None,
    output_dir: Path,
) -> Path:
    """Expensive last-resort gplaydl path with a fresh local JP dispenser."""
    remaining = _remaining_budget()
    if remaining is not None and remaining < 15:
        raise GooglePlayTimeout(
            "not enough Google Play budget remains to bootstrap the fresh-profile fallback"
        )
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
            executable, package, downloads, exact_code, profile_retry=True
        )
        logging.info(
            "🔐 Retrying Google Play with a fresh local JP device profile: package=%s%s",
            package,
            f" exact-versionCode={candidate.code} ({candidate.name})" if exact_code and candidate else " current release",
        )
        result = _run(command)
        if result.returncode == 0:
            return _collect_linked_download(downloads, package, output_dir, result)
        tail = "\n".join((result.stdout or "").splitlines()[-20:])
        raise RuntimeError(
            "fresh-profile gplaydl exited non-zero: "
            f"{_secret_safe_text(tail).strip() or 'no diagnostic output'}"
        )


def _begin_budget() -> contextvars.Token:
    total = _env_seconds("GPLAY_TOTAL_BUDGET_SECONDS", _TOTAL_BUDGET_SECONDS)
    return _play_deadline.set(time.monotonic() + total)


def _validate_current_fallback(
    path: Path,
    package: str,
    candidate: VersionCandidate,
    label: str,
) -> Path:
    try:
        identity = apk_identity.validate_identity(path, package, candidate)
    except Exception:
        path.unlink(missing_ok=True)
        raise
    logging.info(
        "✅ %s current Play release exactly matches requested candidate: "
        "package=%s versionName=%s versionCode=%s",
        label,
        identity.package_name,
        identity.version_name,
        identity.version_code or "unknown",
    )
    return path


def download_candidate(
    package: str,
    candidate: VersionCandidate | None,
    output_dir: Path | None = None,
) -> Path:
    if not google_play_enabled(package):
        raise GooglePlayDisabled(
            f"Google Play is disabled by repository policy for {package}; use GitHub"
        )

    play_candidate = play_version_resolver.resolve_candidate(package, candidate)
    output_dir = output_dir or Path(".")
    output_dir.mkdir(parents=True, exist_ok=True)
    token = _begin_budget()
    try:
        errors: list[tuple[str, Exception]] = []

        try:
            return _download_with_fast_gplaydl(package, play_candidate, output_dir)
        except Exception as error:
            errors.append(("gplaydl", error))
            logging.warning(
                "⚠️  Fast gplaydl failed for %s: %s; trying playfetch",
                package,
                _secret_safe_text(str(error)),
            )

        if play_candidate is None:
            try:
                return _download_with_playfetch_google_play(package, output_dir)
            except Exception as error:
                errors.append(("playfetch", error))
                logging.warning(
                    "⚠️  playfetch failed for %s: %s; trying apkeep",
                    package,
                    _secret_safe_text(str(error)),
                )
            try:
                return _download_with_apkeep_google_play(package, output_dir)
            except Exception as error:
                errors.append(("apkeep", error))
                logging.warning(
                    "⚠️  apkeep failed for %s: %s; trying fresh-profile gplaydl safety net",
                    package,
                    _secret_safe_text(str(error)),
                )
            try:
                return _download_with_linked_gplaydl(package, None, output_dir)
            except Exception as error:
                errors.append(("fresh-profile-gplaydl", error))
        else:
            try:
                current = _download_with_playfetch_google_play(package, output_dir)
                return _validate_current_fallback(
                    current, package, play_candidate, "playfetch"
                )
            except Exception as error:
                errors.append(("playfetch-current-probe", error))
                logging.warning(
                    "⚠️  playfetch current release did not satisfy exact %s for %s; trying apkeep",
                    play_candidate.describe(),
                    package,
                )
            try:
                current = _download_with_apkeep_google_play(package, output_dir)
                return _validate_current_fallback(
                    current, package, play_candidate, "apkeep"
                )
            except Exception as error:
                errors.append(("apkeep-current-probe", error))
            try:
                return _download_with_linked_gplaydl(
                    package, play_candidate, output_dir
                )
            except Exception as error:
                errors.append(("fresh-profile-gplaydl", error))

        detail = "; ".join(
            f"{name}: {_secret_safe_text(str(error))}" for name, error in errors
        )
        raise RuntimeError(f"all Google Play download paths failed. {detail}")
    finally:
        _play_deadline.reset(token)


def download_current(package: str, output_dir: Path | None = None) -> Path:
    return download_candidate(package, None, output_dir)
