"""Ephemeral self-hosted gplaydl-dispenser for CI.

When ``GPLAY_EMAIL`` and ``GPLAY_AAS_TOKEN`` are available, this module starts
an isolated PostgreSQL container and a locally-built copy of the current
``rehmatworks/gplaydl-dispenser``. The upstream purchase/delivery logic remains
owned by gplaydl; only the dispenser's token-mint market metadata is made
configurable before it is built.

Nothing is exposed outside the runner. The database, encryption key, API key,
and dispenser process exist only for the lifetime of the download process.
"""

from __future__ import annotations

import atexit
import json
import logging
import os
import secrets
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

UPSTREAM_REPOSITORY = "https://github.com/rehmatworks/gplaydl-dispenser.git"
DEFAULT_UPSTREAM_REF = "main"
POSTGRES_IMAGE = "postgres:18-alpine"
POSTGRES_PORT = 5466
DISPENSER_PORT = 18080

_runtime_root: Path | None = None
_postgres_name: str | None = None
_dispenser_process: subprocess.Popen[str] | None = None
_log_handle = None
_started = False


def _strict_replace(text: str, old: str, new: str, label: str) -> str:
    if text.count(old) != 1:
        raise RuntimeError(
            f"upstream gplaydl-dispenser changed around {label}; "
            "refusing to apply a stale CI market patch"
        )
    return text.replace(old, new, 1)


def patch_upstream_market(checkout: Path) -> None:
    """Make current dispenser token-mint market metadata environment-driven.

    Defaults preserve upstream behavior. CI explicitly supplies JP values, so
    there are no app/package-specific conditions and no change to purchase or
    delivery semantics.
    """
    path = checkout / "internal" / "gplay" / "client.go"
    text = path.read_text(encoding="utf-8")

    text = _strict_replace(
        text,
        '\t"net/url"\n',
        '\t"net/url"\n\t"os"\n',
        "client imports",
    )
    text = _strict_replace(
        text,
        "func (c *Client) Mint(ctx context.Context, account Account, dc DeviceConfig, locale, proxyURL string) (*AuthBundle, error) {\n",
        "func (c *Client) Mint(ctx context.Context, account Account, dc DeviceConfig, locale, proxyURL string) (*AuthBundle, error) {\n"
        '\tlocale = envOr("GPLAY_DEFAULT_LOCALE", locale)\n'
        "\tdc = marketDeviceConfig(dc, locale)\n",
        "Mint market normalization",
    )
    text = _strict_replace(
        text,
        '\t\t\tMccMnc:              "310260",\n',
        '\t\t\tMccMnc:              envOr("GPLAY_MCCMNC", "310260"),\n',
        "AuthBundle MCC/MNC",
    )
    text = _strict_replace(
        text,
        '\t\t"device_country":               {"IN"},\n',
        '\t\t"device_country":               {envOr("GPLAY_DEVICE_COUNTRY", "IN")},\n',
        "OAuth device_country",
    )
    text = _strict_replace(
        text,
        '\th.Set("X-DFE-MCCMNC", "21601")\n',
        '\th.Set("X-DFE-MCCMNC", envOr("GPLAY_MCCMNC", "21601"))\n',
        "FDFE MCC/MNC",
    )

    helper = r'''

func envOr(name, fallback string) string {
	if value := strings.TrimSpace(os.Getenv(name)); value != "" {
		return value
	}
	return fallback
}

func marketDeviceConfig(dc DeviceConfig, locale string) DeviceConfig {
	mccmnc := strings.TrimSpace(os.Getenv("GPLAY_MCCMNC"))
	timezone := strings.TrimSpace(os.Getenv("GPLAY_TIMEZONE"))
	if mccmnc == "" && timezone == "" && locale == "" {
		return dc
	}

	out := make(DeviceConfig, len(dc)+4)
	for key, value := range dc {
		out[key] = value
	}
	if len(mccmnc) == 5 || len(mccmnc) == 6 {
		out["CellOperator"] = mccmnc[:3]
		out["SimOperator"] = mccmnc[3:]
	}
	if timezone != "" {
		out["TimeZone"] = timezone
	}
	if locale != "" {
		out["locale"] = locale
	}
	return out
}
'''
    if "func envOr(name, fallback string) string" in text:
        raise RuntimeError("upstream already contains CI market helper; patch needs review")
    text = text.rstrip() + helper + "\n"
    path.write_text(text, encoding="utf-8")


def _run(command: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )


def _must_run(command: list[str], *, cwd: Path | None = None, label: str) -> str:
    result = _run(command, cwd=cwd)
    if result.returncode != 0:
        tail = "\n".join((result.stdout or "").splitlines()[-30:])
        raise RuntimeError(f"{label} failed: {tail}")
    return result.stdout or ""


def _wait_http(url: str, timeout_seconds: int = 45) -> None:
    deadline = time.monotonic() + timeout_seconds
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if response.status == 200:
                    return
        except Exception as error:  # noqa: BLE001 - readiness polling
            last_error = error
        time.sleep(0.5)
    raise RuntimeError(f"local gplaydl dispenser did not become healthy: {last_error}")


def _post_json(url: str, payload: dict, headers: dict[str, str] | None = None) -> dict:
    request_headers = {"Content-Type": "application/json"}
    if headers:
        request_headers.update(headers)
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=request_headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"local dispenser API returned HTTP {error.code}: {body}") from error


def _credentials() -> tuple[str, str] | None:
    email = (os.getenv("GPLAY_EMAIL", "").strip() or os.getenv("GPLAYDL_EMAIL", "").strip())
    aas_token = os.getenv("GPLAY_AAS_TOKEN", "").strip()
    if not email and not aas_token:
        return None
    if not email or not aas_token:
        raise RuntimeError(
            "local gplaydl dispenser requires both GPLAY_EMAIL and GPLAY_AAS_TOKEN"
        )
    if not aas_token.startswith("aas_et/"):
        raise RuntimeError("GPLAY_AAS_TOKEN does not look like an AAS token")
    return email, aas_token


def _cleanup() -> None:
    global _dispenser_process, _postgres_name, _log_handle
    if _dispenser_process is not None:
        _dispenser_process.terminate()
        try:
            _dispenser_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _dispenser_process.kill()
        _dispenser_process = None
    if _log_handle is not None:
        _log_handle.close()
        _log_handle = None
    if _postgres_name:
        _run(["docker", "rm", "-f", _postgres_name])
        _postgres_name = None


atexit.register(_cleanup)


def ensure_running() -> bool:
    """Start and bootstrap the local dispenser when AAS credentials are present."""
    global _runtime_root, _postgres_name, _dispenser_process, _log_handle, _started
    if _started:
        return True

    credentials = _credentials()
    if credentials is None:
        return False
    email, aas_token = credentials

    for executable in ("git", "go", "docker"):
        if not shutil.which(executable):
            raise RuntimeError(f"{executable} is required for the local gplaydl dispenser")

    _runtime_root = Path(tempfile.mkdtemp(prefix="gplaydl-dispenser-ci-"))
    checkout = _runtime_root / "upstream"
    upstream_ref = os.getenv("GPLAYDL_DISPENSER_REF", DEFAULT_UPSTREAM_REF).strip() or DEFAULT_UPSTREAM_REF
    repository = os.getenv("GPLAYDL_DISPENSER_REPOSITORY", UPSTREAM_REPOSITORY).strip() or UPSTREAM_REPOSITORY

    logging.info("🧰 Starting ephemeral self-hosted gplaydl dispenser from upstream %s", upstream_ref)
    _must_run(
        ["git", "clone", "--depth", "1", "--branch", upstream_ref, repository, str(checkout)],
        label="clone upstream gplaydl-dispenser",
    )
    patch_upstream_market(checkout)

    # The API is all CI needs. Satisfy go:embed without building the React UI.
    dist = checkout / "web" / "dist"
    dist.mkdir(parents=True, exist_ok=True)
    (dist / "index.html").write_text("<!doctype html><title>CI dispenser</title>\n", encoding="utf-8")

    binary = _runtime_root / "gplaydl-dispenser"
    _must_run(
        ["go", "build", "-o", str(binary), "./cmd/dispenser"],
        cwd=checkout,
        label="build upstream gplaydl-dispenser",
    )

    postgres_password = secrets.token_hex(16)
    _postgres_name = f"gplaydl-dispenser-db-{os.getpid()}"
    _must_run(
        [
            "docker", "run", "--rm", "-d",
            "--name", _postgres_name,
            "-e", "POSTGRES_USER=dispenser",
            "-e", f"POSTGRES_PASSWORD={postgres_password}",
            "-e", "POSTGRES_DB=dispenser",
            "-p", f"127.0.0.1:{POSTGRES_PORT}:5432",
            POSTGRES_IMAGE,
        ],
        label="start ephemeral Postgres",
    )

    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        ready = _run(["docker", "exec", _postgres_name, "pg_isready", "-U", "dispenser"])
        if ready.returncode == 0:
            break
        time.sleep(0.5)
    else:
        raise RuntimeError("ephemeral Postgres did not become ready")

    server_env = os.environ.copy()
    server_env.update(
        {
            "DISPENSER_ADDR": f"127.0.0.1:{DISPENSER_PORT}",
            "DATABASE_URL": (
                f"postgres://dispenser:{postgres_password}@127.0.0.1:{POSTGRES_PORT}/"
                "dispenser?sslmode=disable"
            ),
            "DISPENSER_ENCRYPTION_KEY": secrets.token_hex(32),
            "DISPENSER_DEV": "1",
            "PUBLIC_URL": f"http://127.0.0.1:{DISPENSER_PORT}",
            "RESOURCES_DIR": str((checkout / "resources").resolve()),
            "GPLAY_DEVICE_COUNTRY": os.getenv("GPLAY_DEVICE_COUNTRY", "JP"),
            "GPLAY_MCCMNC": os.getenv("GPLAY_MCCMNC", "44010"),
            "GPLAY_DEFAULT_LOCALE": os.getenv("GPLAY_DEFAULT_LOCALE", "ja_JP"),
            "GPLAY_TIMEZONE": os.getenv("GPLAY_TIMEZONE", "Asia/Tokyo"),
        }
    )
    log_path = _runtime_root / "dispenser.log"
    _log_handle = log_path.open("w", encoding="utf-8")
    _dispenser_process = subprocess.Popen(
        [str(binary)],
        cwd=checkout,
        env=server_env,
        stdout=_log_handle,
        stderr=subprocess.STDOUT,
        text=True,
    )

    base_url = f"http://127.0.0.1:{DISPENSER_PORT}"
    try:
        _wait_http(base_url + "/api/health")
        enroll = _post_json(
            base_url + "/api/v1/devices/enroll",
            {
                "deviceSecret": secrets.token_urlsafe(48),
                "label": "Morphe GitHub Actions",
                "consentVersion": "ci-bootstrap",
            },
        )
        api_key = str(enroll.get("apiKey", "")).strip()
        if not api_key:
            raise RuntimeError("local dispenser enrollment returned no API key")
        _post_json(
            base_url + "/api/v1/accounts",
            {"email": email, "aasToken": aas_token},
            {"X-Api-Key": api_key},
        )
    except Exception:
        if log_path.exists():
            tail = "\n".join(log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-30:])
            logging.error("local dispenser startup log tail:\n%s", tail)
        _cleanup()
        raise

    # Credentials never appear in argv or logs. gplaydl reads the ephemeral key
    # from its normal CI environment and sends it only to this localhost URL.
    os.environ["GPLAYDL_API_KEY"] = api_key
    os.environ["GPLAYDL_DISPENSER_URL"] = base_url
    _started = True
    logging.info(
        "✅ Ephemeral gplaydl dispenser ready: market_country=%s mccmnc=%s locale=%s",
        server_env["GPLAY_DEVICE_COUNTRY"],
        server_env["GPLAY_MCCMNC"],
        server_env["GPLAY_DEFAULT_LOCALE"],
    )
    return True
