"""Ephemeral self-hosted gplaydl-dispenser for CI.

When ``GPLAY_EMAIL`` and ``GPLAY_AAS_TOKEN`` are available, this module starts
an isolated PostgreSQL container and a locally-built copy of the current
``rehmatworks/gplaydl-dispenser``. The upstream purchase/delivery logic remains
owned by gplaydl; only the dispenser's token-mint market metadata is made
configurable before it is built.

Nothing is exposed outside the runner. The database, encryption key, API key,
and dispenser process exist only for the lifetime of the download process.

Robustness design
-----------------
* Two phases.  ``prepare()`` does the network-heavy, slow work (git clone, patch,
  ``go build``, ``docker pull``) and is idempotent; ``ensure_running()`` only
  starts Postgres + the binary and enrolls.  ``prepare()`` can therefore run as
  its own workflow step (own timeout/log) *before* traffic is routed through a
  Tailscale exit node, using ``GPLAYDL_DISPENSER_WORKDIR`` to hand artifacts over.
* A failed start no longer leaves a Postgres container / port behind, and a
  retry reuses the already built artifacts instead of cloning and building again.
* Postgres reachability through the published port is verified before the
  dispenser starts (see ``_probe_postgres_port``); a Tailscale exit node without
  LAN access silently blackholes Docker traffic and used to look like a hung,
  log-less dispenser.
* Every external command has a timeout; the health wait fails immediately if the
  dispenser process has already exited (previously it polled for the full 45s).
* On failure the real reason is logged (process exit code, dispenser log tail,
  Postgres log tail, secrets redacted) - an empty "log tail" used to be all
  that was visible.
* After ``GPLAYDL_DISPENSER_MAX_START_ATTEMPTS`` (default 2) failed starts the
  failure is remembered and later calls raise immediately instead of redoing
  the whole sequence.  Concurrent calls never start a second dispenser.

CLI: ``python3 -m src.local_gplaydl_dispenser prepare``
"""

from __future__ import annotations

import atexit
import json
import logging
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

log = logging.getLogger(__name__)

UPSTREAM_REPOSITORY = "https://github.com/rehmatworks/gplaydl-dispenser.git"
DEFAULT_UPSTREAM_REF = "main"
POSTGRES_IMAGE = "postgres:18-alpine"
POSTGRES_PORT = 5466
DISPENSER_PORT = 18080

_runtime_root: Path | None = None
_owns_root = False
_postgres_name: str | None = None
_dispenser_process: subprocess.Popen[str] | None = None
_log_handle = None
_log_path: Path | None = None
_started = False

_lock = threading.Lock()
_attempts = 0
_failure: str | None = None
_known_secrets: list[str] = []


def _env_int(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, "").strip() or default)
    except ValueError:
        return default
    return value if value > 0 else default


def _max_attempts() -> int:
    return _env_int("GPLAYDL_DISPENSER_MAX_START_ATTEMPTS", 2)


def _redact(text: str) -> str:
    """Mask credentials and ephemeral secrets before text reaches a log."""
    text = re.sub(r"aas_et/[\w\-./+=]+", "aas_et/***", text)
    for secret in _known_secrets:
        if secret:
            text = text.replace(secret, "***")
    return text


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


def _run(
    command: list[str], *, cwd: Path | None = None, timeout: int | None = None
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            cwd=cwd,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as error:
        partial = error.stdout or ""
        if isinstance(partial, bytes):
            partial = partial.decode("utf-8", errors="replace")
        return subprocess.CompletedProcess(
            command, 124, stdout=f"{partial}\n[timed out after {timeout}s]"
        )


def _must_run(
    command: list[str],
    *,
    cwd: Path | None = None,
    label: str,
    timeout: int | None = None,
) -> str:
    result = _run(command, cwd=cwd, timeout=timeout)
    if result.returncode != 0:
        tail = "\n".join((result.stdout or "").splitlines()[-30:])
        raise RuntimeError(f"{label} failed (exit {result.returncode}): {tail}")
    return result.stdout or ""


def _wait_http(
    url: str,
    timeout_seconds: int = 45,
    process: subprocess.Popen | None = None,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        if process is not None and process.poll() is not None:
            raise RuntimeError(
                f"local gplaydl dispenser process exited with code {process.returncode} "
                "before becoming healthy"
            )
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if response.status == 200:
                    return
        except Exception as error:  # noqa: BLE001 - readiness polling
            last_error = error
        time.sleep(0.5)
    raise RuntimeError(
        f"local gplaydl dispenser did not become healthy within {timeout_seconds}s: "
        f"{last_error}"
    )


def _wait_postgres_final(
    container: str,
    *,
    user: str = "dispenser",
    database: str = "dispenser",
    timeout_seconds: int = 45,
) -> None:
    """Wait for the final postgres process, not Docker's init-time temp server.

    The official postgres image briefly runs a temporary PostgreSQL server
    during first-time initialization. ``pg_isready`` can succeed against that
    server immediately before the entrypoint shuts it down and execs the final
    server. Starting gplaydl-dispenser in that window can make its first DB
    connection reset. PID 1 becomes ``postgres`` only after initialization is
    complete, so require that transition and then prove the target database can
    execute a query before starting the dispenser.
    """
    deadline = time.monotonic() + timeout_seconds
    last_detail = "container entrypoint has not exec'd postgres yet"
    while time.monotonic() < deadline:
        pid1 = _run(["docker", "exec", container, "cat", "/proc/1/comm"], timeout=20)
        if pid1.returncode == 0 and (pid1.stdout or "").strip() == "postgres":
            probe = _run(
                [
                    "docker",
                    "exec",
                    container,
                    "psql",
                    "-qAtX",
                    "-U",
                    user,
                    "-d",
                    database,
                    "-c",
                    "SELECT 1;",
                ],
                timeout=20,
            )
            if probe.returncode == 0 and (probe.stdout or "").strip() == "1":
                return
            last_detail = (probe.stdout or "database probe failed").strip()
        elif pid1.returncode != 0:
            last_detail = (pid1.stdout or "could not inspect container PID 1").strip()
        time.sleep(0.5)

    logs = _run(["docker", "logs", "--tail", "30", container], timeout=20)
    tail = (logs.stdout or "").strip()
    if tail:
        last_detail = f"{last_detail}; postgres log tail:\n{tail}"
    raise RuntimeError(f"ephemeral Postgres did not become ready: {last_detail}")


def _probe_postgres_port(port: int, timeout_seconds: int = 20) -> None:
    """Prove Postgres answers on the *published* localhost port.

    ``docker exec`` (used by _wait_postgres_final) bypasses the network, so it
    succeeds even when the host cannot reach the container.  That is exactly what
    happens when a Tailscale exit node is active without
    ``--exit-node-allow-lan-access``: traffic to Docker's bridge subnet is sent
    to the exit node, the dispenser blocks silently on its first DB connection,
    never logs anything and never turns healthy.  A Postgres SSLRequest round
    trip detects this in seconds and says why.
    """
    deadline = time.monotonic() + timeout_seconds
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=3) as sock:
                sock.settimeout(3)
                sock.sendall(b"\x00\x00\x00\x08\x04\xd2\x16\x2f")  # SSLRequest
                if sock.recv(1) in (b"S", b"N"):
                    return
                last_error = RuntimeError("connection closed without a Postgres reply")
        except Exception as error:  # noqa: BLE001 - readiness polling
            last_error = error
        time.sleep(1)
    raise RuntimeError(
        f"Postgres is not reachable on 127.0.0.1:{port} from the host ({last_error}). "
        "If a Tailscale exit node is active, run it with --exit-node-allow-lan-access=true "
        "so Docker bridge traffic is not sent to the exit node"
    )


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


def _stop() -> None:
    """Stop the dispenser process and Postgres container (artifacts are kept)."""
    global _dispenser_process, _postgres_name, _log_handle
    if _dispenser_process is not None:
        try:
            _dispenser_process.terminate()
            try:
                _dispenser_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                _dispenser_process.kill()
        except Exception:  # noqa: BLE001 - best-effort cleanup
            pass
        _dispenser_process = None
    if _log_handle is not None:
        try:
            _log_handle.close()
        except Exception:  # noqa: BLE001
            pass
        _log_handle = None
    if _postgres_name:
        _run(["docker", "rm", "-f", _postgres_name], timeout=30)
        _postgres_name = None


def _cleanup() -> None:
    global _runtime_root, _started
    _stop()
    _started = False
    if _runtime_root is not None and _owns_root:
        shutil.rmtree(_runtime_root, ignore_errors=True)
        _runtime_root = None


atexit.register(_cleanup)


def _check_tools() -> None:
    for executable in ("git", "go", "docker"):
        if not shutil.which(executable):
            raise RuntimeError(f"{executable} is required for the local gplaydl dispenser")


def _workdir() -> Path:
    """Directory holding upstream checkout + binary; reused across attempts."""
    global _runtime_root, _owns_root
    if _runtime_root is not None:
        return _runtime_root
    configured = os.getenv("GPLAYDL_DISPENSER_WORKDIR", "").strip()
    if configured:
        _runtime_root = Path(configured)
        _runtime_root.mkdir(parents=True, exist_ok=True)
        _owns_root = False
    else:
        _runtime_root = Path(tempfile.mkdtemp(prefix="gplaydl-dispenser-ci-"))
        _owns_root = True
    return _runtime_root


def _prepare(root: Path) -> None:
    """Clone, patch, build and pull images. Idempotent and network-heavy."""
    upstream_ref = os.getenv("GPLAYDL_DISPENSER_REF", DEFAULT_UPSTREAM_REF).strip() or DEFAULT_UPSTREAM_REF
    repository = os.getenv("GPLAYDL_DISPENSER_REPOSITORY", UPSTREAM_REPOSITORY).strip() or UPSTREAM_REPOSITORY
    checkout = root / "upstream"
    binary = root / "gplaydl-dispenser"
    marker = root / ".prepared"
    stamp = f"{repository}@{upstream_ref}"

    if not (binary.exists() and marker.exists() and marker.read_text(encoding="utf-8") == stamp):
        t0 = time.monotonic()
        # A partially prepared tree (e.g. build failed) is already patched, and
        # re-patching it would be refused as "stale"; always start from scratch.
        shutil.rmtree(checkout, ignore_errors=True)
        binary.unlink(missing_ok=True)

        log.info("🧰 Preparing local gplaydl dispenser from upstream %s", upstream_ref)
        _must_run(
            ["git", "clone", "--depth", "1", "--branch", upstream_ref, repository, str(checkout)],
            label="clone upstream gplaydl-dispenser",
            timeout=_env_int("GPLAYDL_DISPENSER_CLONE_TIMEOUT", 180),
        )
        patch_upstream_market(checkout)

        # The API is all CI needs. Satisfy go:embed without building the React UI.
        dist = checkout / "web" / "dist"
        dist.mkdir(parents=True, exist_ok=True)
        (dist / "index.html").write_text("<!doctype html><title>CI dispenser</title>\n", encoding="utf-8")

        _must_run(
            ["go", "build", "-o", str(binary), "./cmd/dispenser"],
            cwd=checkout,
            label="build upstream gplaydl-dispenser",
            timeout=_env_int("GPLAYDL_DISPENSER_BUILD_TIMEOUT", 600),
        )
        marker.write_text(stamp, encoding="utf-8")
        log.info("   clone/patch/build finished in %.0fs", time.monotonic() - t0)

    if _run(["docker", "image", "inspect", POSTGRES_IMAGE], timeout=30).returncode != 0:
        t0 = time.monotonic()
        _must_run(
            ["docker", "pull", POSTGRES_IMAGE],
            label=f"pull {POSTGRES_IMAGE}",
            timeout=_env_int("GPLAYDL_DISPENSER_PULL_TIMEOUT", 300),
        )
        log.info("   pulled %s in %.0fs", POSTGRES_IMAGE, time.monotonic() - t0)


def _start(root: Path, email: str, aas_token: str) -> tuple[str, str]:
    """Start Postgres + dispenser and enroll. Returns (api_key, base_url)."""
    global _postgres_name, _dispenser_process, _log_handle, _log_path
    checkout = root / "upstream"
    binary = root / "gplaydl-dispenser"

    postgres_password = secrets.token_hex(16)
    _known_secrets.append(postgres_password)
    _postgres_name = f"gplaydl-dispenser-db-{os.getpid()}"
    _run(["docker", "rm", "-f", _postgres_name], timeout=30)  # stale leftovers
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
        timeout=120,
    )

    _wait_postgres_final(_postgres_name)
    _probe_postgres_port(POSTGRES_PORT)

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
    _log_path = root / "dispenser.log"
    _log_handle = _log_path.open("w", encoding="utf-8")
    _dispenser_process = subprocess.Popen(
        [str(binary)],
        cwd=checkout,
        env=server_env,
        stdout=_log_handle,
        stderr=subprocess.STDOUT,
        text=True,
    )

    base_url = f"http://127.0.0.1:{DISPENSER_PORT}"
    _wait_http(
        base_url + "/api/health",
        _env_int("GPLAYDL_DISPENSER_HEALTH_TIMEOUT", 45),
        _dispenser_process,
    )
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
    _known_secrets.append(api_key)
    _post_json(
        base_url + "/api/v1/accounts",
        {"email": email, "aasToken": aas_token},
        {"X-Api-Key": api_key},
    )
    return api_key, base_url


def _diagnostics() -> str:
    """Why did the start fail? Must run before _stop()."""
    parts: list[str] = []
    proc = _dispenser_process
    if proc is not None:
        code = proc.poll()
        parts.append(
            "dispenser process: "
            + (f"exited with code {code}" if code is not None else "still running (not healthy)")
        )
    if _log_handle is not None:
        try:
            _log_handle.flush()
        except Exception:  # noqa: BLE001
            pass
    if _log_path is not None and _log_path.exists():
        lines = _log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-30:]
        parts.append("dispenser log tail:\n" + ("\n".join(lines) or "(empty)"))
    if _postgres_name:
        logs = _run(["docker", "logs", "--tail", "15", _postgres_name], timeout=20)
        parts.append("postgres log tail:\n" + ((logs.stdout or "").strip() or "(empty)"))
    return _redact("\n".join(parts))


def prepare() -> bool:
    """Build artifacts only (no processes started). Safe to run before routing
    traffic through an exit node.  Returns False when no credentials exist."""
    if _credentials() is None:
        log.info("No Google Play credentials; skipping local dispenser preparation")
        return False
    _check_tools()
    _prepare(_workdir())
    return True


def ensure_running() -> bool:
    """Start and bootstrap the local dispenser when AAS credentials are present."""
    global _started, _attempts, _failure
    if _started:
        return True

    credentials = _credentials()
    if credentials is None:
        return False
    email, aas_token = credentials
    _known_secrets.extend([email, aas_token])

    if _failure is not None and _attempts >= _max_attempts():
        raise RuntimeError(f"local gplaydl dispenser unavailable (not retrying): {_failure}")
    if not _lock.acquire(blocking=False):
        raise RuntimeError("local gplaydl dispenser startup already in progress")
    try:
        if _started:
            return True
        _attempts += 1
        try:
            _check_tools()
            root = _workdir()
            _prepare(root)
            api_key, base_url = _start(root, email, aas_token)
        except Exception as error:  # noqa: BLE001
            diagnostics = _diagnostics()
            reason = _redact(str(error)).strip().splitlines()
            _failure = (reason[0] if reason else type(error).__name__)[:300]
            log.error(
                "local dispenser startup failed (attempt %d/%d): %s\n%s",
                _attempts,
                _max_attempts(),
                _redact(str(error))[:2000],
                diagnostics,
            )
            _stop()
            raise RuntimeError(_failure) from None

        # Credentials never appear in argv or logs. gplaydl reads the ephemeral key
        # from its normal CI environment and sends it only to this localhost URL.
        os.environ["GPLAYDL_API_KEY"] = api_key
        os.environ["GPLAYDL_DISPENSER_URL"] = base_url
        _started = True
        log.info(
            "✅ Ephemeral gplaydl dispenser ready: market_country=%s mccmnc=%s locale=%s",
            os.getenv("GPLAY_DEVICE_COUNTRY", "JP"),
            os.getenv("GPLAY_MCCMNC", "44010"),
            os.getenv("GPLAY_DEFAULT_LOCALE", "ja_JP"),
        )
        return True
    finally:
        _lock.release()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    if sys.argv[1:] != ["prepare"]:
        print("usage: python3 -m src.local_gplaydl_dispenser prepare", file=sys.stderr)
        sys.exit(2)
    if not os.getenv("GPLAYDL_DISPENSER_WORKDIR", "").strip():
        print("GPLAYDL_DISPENSER_WORKDIR must be set so artifacts outlive this process",
              file=sys.stderr)
        sys.exit(2)
    try:
        prepare()
    except Exception as error:  # noqa: BLE001
        log.error("prepare failed: %s", _redact(str(error))[:2000])
        sys.exit(1)
