"""Narrow Google Play fallback using Aurora Store-style anonymous auth.

This is intentionally not a normal/default APK provider. Third-party mirrors
occasionally advertise one version while serving another; callers may opt in to
this fallback and must still validate the downloaded manifest identity before
using or caching the APK.

Aurora Store anonymous login posts device properties to its token dispenser and
receives an email plus Google AUTH token. We mirror that protocol, then let the
already-installed apkeep binary talk to Google Play. The device profile is read
from EFForg/rs-google-play, the same profile source documented by apkeep, so we
do not freeze a guessed device fingerprint in this repository.
"""

from __future__ import annotations

import configparser
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.request import Request, urlopen

AURORA_DISPENSER_URL = "https://auroraoss.com/api/auth"
DEVICE_PROPERTIES_URL = (
    "https://raw.githubusercontent.com/EFForg/rs-google-play/master/"
    "gpapi/device.properties"
)
DEFAULT_DEVICE = "px_9a"
DEFAULT_AURORA_USER_AGENT = "com.aurora.store-4.8.4-76"


def _read_url(url: str, *, timeout: int = 30) -> bytes:
    request = Request(url, headers={"User-Agent": DEFAULT_AURORA_USER_AGENT})
    with urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed HTTPS URL
        return response.read()


def _device_properties(device: str = DEFAULT_DEVICE) -> dict[str, str]:
    """Load one current rs-google-play device profile as a JSON-ready mapping."""
    text = _read_url(DEVICE_PROPERTIES_URL).decode("utf-8")
    parser = configparser.ConfigParser(
        interpolation=None,
        strict=False,
        delimiters=("=",),
        comment_prefixes=("#", ";"),
    )
    parser.optionxform = str
    parser.read_string(text)
    if not parser.has_section(device):
        raise RuntimeError(f"rs-google-play device profile not found: {device}")

    properties: dict[str, str] = {}
    for key, raw_value in parser.items(device):
        # device.properties uses Java Properties escaping. The Aurora endpoint
        # receives kotlinx-serialized Properties, so remove the escaping that
        # only exists for the source file syntax.
        value = raw_value.replace("\\:", ":").replace("\\=", "=")
        properties[key] = value
    if not properties.get("Build.FINGERPRINT") or not properties.get("Build.MODEL"):
        raise RuntimeError(f"device profile {device} is incomplete")
    return properties


def _anonymous_auth(
    *,
    dispenser_url: str | None = None,
    device: str | None = None,
    timeout: int = 30,
) -> tuple[str, str]:
    """Obtain Aurora anonymous account email + AUTH token without logging it."""
    dispenser_url = dispenser_url or os.getenv("AURORA_DISPENSER_URL") or AURORA_DISPENSER_URL
    device = device or os.getenv("AURORA_DEVICE_PROFILE") or DEFAULT_DEVICE
    body = json.dumps(_device_properties(device), separators=(",", ":")).encode("utf-8")
    request = Request(
        dispenser_url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "User-Agent": os.getenv("AURORA_USER_AGENT") or DEFAULT_AURORA_USER_AGENT,
        },
        method="POST",
    )
    with urlopen(request, timeout=timeout) as response:  # noqa: S310 - configured HTTPS endpoint
        payload = json.loads(response.read().decode("utf-8"))
    email = str(payload.get("email") or "").strip()
    token = str(payload.get("authToken") or "").strip()
    if not email or not token:
        raise RuntimeError("Aurora dispenser returned no email/authToken")
    return email, token


def _find_apkeep() -> str:
    executable = shutil.which("apkeep")
    candidates = (
        (Path(".venv") / "Scripts" / "apkeep.exe",)
        if sys.platform == "win32"
        else (Path(".venv") / "bin" / "apkeep",)
    )
    for candidate in candidates:
        if executable is None and candidate.is_file():
            executable = str(candidate)
            break
    if executable is None:
        raise FileNotFoundError("apkeep executable was not found")
    return executable


def download_current(
    package: str,
    output_dir: Path | None = None,
    *,
    device: str | None = None,
) -> Path:
    """Download the Play Store's current APK using an Aurora anonymous account.

    Google Play does not accept apkeep's APKPure-style ``package@version``
    selector. The caller therefore MUST inspect the resulting APK manifest and
    reject it unless its version equals the patch-compatible target.
    """
    output_dir = output_dir or Path(".")
    output_dir.mkdir(parents=True, exist_ok=True)
    executable = _find_apkeep()
    device = device or os.getenv("AURORA_DEVICE_PROFILE") or DEFAULT_DEVICE
    email, token = _anonymous_auth(device=device)

    # Isolate output so a pre-existing APK can never be mistaken for this
    # request's result. Keep the AUTH token out of our own logs.
    with tempfile.TemporaryDirectory(prefix="aurora-play-", dir=output_dir) as tmp:
        tmp_dir = Path(tmp)
        command = [
            executable,
            "-a",
            package,
            "-d",
            "google-play",
            "-e",
            email,
            "--auth-token",
            token,
            "--accept-tos",
            "-o",
            f"device={device}",
            str(tmp_dir),
        ]
        logging.info(
            "🌌 Aurora anonymous Google Play fallback: package=%s device=%s",
            package,
            device,
        )
        result = subprocess.run(
            command,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        if result.returncode != 0:
            tail = "\n".join((result.stdout or "").splitlines()[-20:])
            raise RuntimeError(
                "apkeep Google Play download failed"
                + (f": {tail}" if tail else "")
            )

        candidates = [
            path
            for path in tmp_dir.rglob("*")
            if path.is_file()
            and path.suffix.lower() in {".apk", ".apkm", ".apks", ".xapk"}
        ]
        if not candidates:
            raise IOError(f"apkeep Google Play produced no APK for {package}")
        source = max(candidates, key=lambda path: path.stat().st_mtime_ns)
        target = output_dir / source.name
        counter = 1
        while target.exists():
            target = output_dir / f"{source.stem}-{counter}{source.suffix}"
            counter += 1
        shutil.copy2(source, target)
        return target
