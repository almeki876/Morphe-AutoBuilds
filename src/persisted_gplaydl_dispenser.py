"""Persistent encrypted CI state for a self-hosted gplaydl dispenser.

The official gplaydl CLI remains unchanged. This module runs the current
``rehmatworks/gplaydl-dispenser`` locally, using the same generic market patch
as :mod:`src.local_gplaydl_dispenser`, and restores Authenticator account state
from a doubly-encrypted PostgreSQL snapshot.
"""

from __future__ import annotations

import atexit
import hashlib
import json
import logging
import os
import secrets
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from src import local_gplaydl_dispenser as upstream_runtime

POSTGRES_IMAGE = "postgres:18-alpine"
POSTGRES_PORT = 5467
DISPENSER_PORT = 18081
STATE_FORMAT_VERSION = "v1"
STATE_FILENAME = "gplaydl-dispenser-state.enc"

_runtime_root: Path | None = None
_postgres_name: str | None = None
_postgres_password: str | None = None
_dispenser_process: subprocess.Popen[str] | None = None
_log_handle = None
_started = False


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


def _must_run(command: list[str], *, cwd: Path | None = None, env: dict[str, str] | None = None, label: str) -> str:
    result = _run(command, cwd=cwd, env=env)
    if result.returncode != 0:
        tail = "\n".join((result.stdout or "").splitlines()[-30:])
        raise RuntimeError(f"{label} failed: {tail}")
    return result.stdout or ""


def _api_key() -> str:
    value = os.getenv("GPLAYDL_API_KEY", "").strip()
    if not value:
        raise RuntimeError("GPLAYDL_API_KEY is required for encrypted self-hosted Google Play state")
    return value


def _derive_hex(purpose: str) -> str:
    prefix = f"Morphe:gplaydl-dispenser:{purpose}:{STATE_FORMAT_VERSION}\0".encode()
    return hashlib.sha256(prefix + _api_key().encode()).hexdigest()


def _server_encryption_key() -> str:
    return _derive_hex("database")


def _snapshot_password() -> str:
    return _derive_hex("snapshot")


def _ci_api_key_hash() -> str:
    return hashlib.sha256(_api_key().encode()).hexdigest()


def configured_state_file() -> Path | None:
    raw = os.getenv("GPLAYDL_STATE_FILE", "").strip()
    if not raw:
        return None
    path = Path(raw).expanduser()
    return path if path.is_file() else None


def _wait_http(url: str, timeout_seconds: int = 45) -> None:
    deadline = time.monotonic() + timeout_seconds
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if response.status == 200:
                    return
        except Exception as error:  # noqa: BLE001
            last_error = error
        time.sleep(0.5)
    raise RuntimeError(f"local persisted gplaydl dispenser did not become healthy: {last_error}")


def _get_json(url: str, headers: dict[str, str] | None = None) -> dict:
    request = urllib.request.Request(url, headers=headers or {}, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"local dispenser API returned HTTP {error.code}: {body}") from error


def _prepare_upstream() -> tuple[Path, Path]:
    global _runtime_root
    for executable in ("git", "go", "docker", "openssl"):
        if not shutil.which(executable):
            raise RuntimeError(f"{executable} is required for encrypted self-hosted Google Play state")

    _runtime_root = Path(tempfile.mkdtemp(prefix="gplaydl-persisted-ci-"))
    checkout = _runtime_root / "upstream"
    upstream_ref = os.getenv("GPLAYDL_DISPENSER_REF", upstream_runtime.DEFAULT_UPSTREAM_REF).strip() or upstream_runtime.DEFAULT_UPSTREAM_REF
    repository = os.getenv("GPLAYDL_DISPENSER_REPOSITORY", upstream_runtime.UPSTREAM_REPOSITORY).strip() or upstream_runtime.UPSTREAM_REPOSITORY

    logging.info("🗄️ Preparing persisted self-hosted gplaydl dispenser from upstream %s", upstream_ref)
    _must_run(
        ["git", "clone", "--depth", "1", "--branch", upstream_ref, repository, str(checkout)],
        label="clone upstream gplaydl-dispenser",
    )
    upstream_runtime.patch_upstream_market(checkout)

    dist = checkout / "web" / "dist"
    dist.mkdir(parents=True, exist_ok=True)
    (dist / "index.html").write_text("<!doctype html><title>CI dispenser</title>\n", encoding="utf-8")

    binary = _runtime_root / "gplaydl-dispenser"
    _must_run(["go", "build", "-o", str(binary), "./cmd/dispenser"], cwd=checkout, label="build upstream gplaydl-dispenser")
    return checkout, binary


def _start_postgres() -> None:
    global _postgres_name, _postgres_password
    _postgres_password = secrets.token_hex(16)
    _postgres_name = f"gplaydl-persisted-db-{os.getpid()}"
    _must_run(
        [
            "docker", "run", "--rm", "-d", "--name", _postgres_name,
            "-e", "POSTGRES_USER=dispenser",
            "-e", f"POSTGRES_PASSWORD={_postgres_password}",
            "-e", "POSTGRES_DB=dispenser",
            "-p", f"127.0.0.1:{POSTGRES_PORT}:5432",
            POSTGRES_IMAGE,
        ],
        label="start persisted-state Postgres",
    )
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        if _run(["docker", "exec", _postgres_name, "pg_isready", "-U", "dispenser"]).returncode == 0:
            return
        time.sleep(0.5)
    raise RuntimeError("persisted-state Postgres did not become ready")


def _server_env(checkout: Path, public_url: str) -> dict[str, str]:
    if _postgres_password is None:
        raise RuntimeError("Postgres is not started")
    env = os.environ.copy()
    env.update(
        {
            "DISPENSER_ADDR": f"127.0.0.1:{DISPENSER_PORT}",
            "DATABASE_URL": f"postgres://dispenser:{_postgres_password}@127.0.0.1:{POSTGRES_PORT}/dispenser?sslmode=disable",
            "DISPENSER_ENCRYPTION_KEY": _server_encryption_key(),
            "DISPENSER_DEV": "1",
            "PUBLIC_URL": public_url,
            "RESOURCES_DIR": str((checkout / "resources").resolve()),
            "GPLAY_DEVICE_COUNTRY": os.getenv("GPLAY_DEVICE_COUNTRY", "JP"),
            "GPLAY_MCCMNC": os.getenv("GPLAY_MCCMNC", "44010"),
            "GPLAY_DEFAULT_LOCALE": os.getenv("GPLAY_DEFAULT_LOCALE", "ja_JP"),
            "GPLAY_TIMEZONE": os.getenv("GPLAY_TIMEZONE", "Asia/Tokyo"),
        }
    )
    return env


def _start_dispenser(binary: Path, checkout: Path, public_url: str) -> dict[str, str]:
    global _dispenser_process, _log_handle
    if _runtime_root is None:
        raise RuntimeError("runtime is not prepared")
    env = _server_env(checkout, public_url)
    _log_handle = (_runtime_root / "dispenser.log").open("w", encoding="utf-8")
    _dispenser_process = subprocess.Popen(
        [str(binary)], cwd=checkout, env=env,
        stdout=_log_handle, stderr=subprocess.STDOUT, text=True,
    )
    _wait_http(f"http://127.0.0.1:{DISPENSER_PORT}/api/health")
    return env


def _crypt_snapshot(source: Path, target: Path, *, decrypt: bool) -> None:
    env = os.environ.copy()
    env["MORPHE_GPLAY_STATE_KEY"] = _snapshot_password()
    command = [
        "openssl", "enc", "-aes-256-cbc", "-pbkdf2", "-iter", "200000",
        "-md", "sha256", "-pass", "env:MORPHE_GPLAY_STATE_KEY",
        "-in", str(source), "-out", str(target),
    ]
    if decrypt:
        command.insert(2, "-d")
    _must_run(command, env=env, label="decrypt Google Play state" if decrypt else "encrypt Google Play state")


def _postgres_query(sql: str) -> str:
    if not _postgres_name:
        raise RuntimeError("Postgres is not started")
    return _must_run(
        ["docker", "exec", _postgres_name, "psql", "-U", "dispenser", "-d", "dispenser", "-At", "-c", sql],
        label="query persisted-state Postgres",
    ).strip()


def registration_account_count() -> int:
    return int(_postgres_query("SELECT count(*) FROM accounts WHERE source = 'app';") or "0")


def attach_ci_api_key() -> None:
    owner_id = _postgres_query(
        "SELECT owner_id::text FROM accounts WHERE source = 'app' ORDER BY created_at DESC LIMIT 1;"
    )
    if not owner_id:
        raise RuntimeError("no Authenticator-synced account exists yet")
    owner_id = owner_id.replace("'", "''")
    key_hash = _ci_api_key_hash().replace("'", "''")
    _postgres_query(
        "INSERT INTO api_keys (user_id, key_hash, label) "
        f"VALUES ('{owner_id}'::uuid, '{key_hash}', 'Morphe GitHub Actions') "
        "ON CONFLICT (key_hash) DO UPDATE SET user_id = EXCLUDED.user_id, label = EXCLUDED.label;"
    )


def validate_registered_account() -> dict[str, str]:
    params = urllib.parse.urlencode({"full": "1", "locale": os.getenv("GPLAY_DEFAULT_LOCALE", "ja_JP")})
    data = _get_json(
        f"http://127.0.0.1:{DISPENSER_PORT}/api/auth?{params}",
        {"X-Api-Key": _api_key()},
    )
    if not data.get("authToken"):
        raise RuntimeError("registered account did not mint a Play auth token")
    device = data.get("deviceInfoProvider") or {}
    return {
        "mccmnc": str(device.get("mccMnc") or ""),
        "locale": str(data.get("locale") or ""),
    }


def snapshot_state(target: Path) -> Path:
    if not _postgres_name or _runtime_root is None:
        raise RuntimeError("registration Postgres is not running")
    target.parent.mkdir(parents=True, exist_ok=True)
    dump = _runtime_root / "snapshot.dump"
    container_dump = "/tmp/gplaydl-state-export.dump"
    _must_run(
        ["docker", "exec", _postgres_name, "pg_dump", "-U", "dispenser", "-d", "dispenser", "-Fc", "-f", container_dump],
        label="dump Google Play dispenser state",
    )
    _must_run(["docker", "cp", f"{_postgres_name}:{container_dump}", str(dump)], label="copy Google Play state dump")
    _crypt_snapshot(dump, target, decrypt=False)
    return target


def start_registration_server() -> str:
    global _started
    _api_key()
    checkout, binary = _prepare_upstream()
    _start_postgres()
    _start_dispenser(binary, checkout, f"http://127.0.0.1:{DISPENSER_PORT}")
    _started = True
    return f"http://127.0.0.1:{DISPENSER_PORT}"


def _restore_state(state_file: Path) -> None:
    if _runtime_root is None or not _postgres_name:
        raise RuntimeError("runtime is not prepared")
    dump = _runtime_root / "restore.dump"
    _crypt_snapshot(state_file, dump, decrypt=True)
    container_dump = "/tmp/gplaydl-state-restore.dump"
    _must_run(["docker", "cp", str(dump), f"{_postgres_name}:{container_dump}"], label="copy Google Play state into Postgres")
    _must_run(
        ["docker", "exec", _postgres_name, "pg_restore", "-U", "dispenser", "-d", "dispenser", "--no-owner", "--no-privileges", container_dump],
        label="restore Google Play dispenser state",
    )


def ensure_running() -> bool:
    """Restore encrypted state and start localhost dispenser; return False on cache miss."""
    global _started
    if _started:
        return True
    state_file = configured_state_file()
    if state_file is None:
        return False

    _api_key()
    try:
        checkout, binary = _prepare_upstream()
        _start_postgres()
        _restore_state(state_file)
        base_url = f"http://127.0.0.1:{DISPENSER_PORT}"
        env = _start_dispenser(binary, checkout, base_url)
        os.environ["GPLAYDL_DISPENSER_URL"] = base_url
        _started = True
        logging.info(
            "✅ Restored encrypted gplaydl dispenser state: country=%s mccmnc=%s locale=%s",
            env["GPLAY_DEVICE_COUNTRY"], env["GPLAY_MCCMNC"], env["GPLAY_DEFAULT_LOCALE"],
        )
        return True
    except Exception:
        _cleanup()
        raise


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
