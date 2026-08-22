#!/usr/bin/env python3
"""Register a Google Play account into GitHub Actions secrets without token output.

This helper is intended for the manually dispatched registration workflow. It:

1. builds the current rehmatworks/gplaydl-dispenser with the same generic
   environment-driven market patch used by normal CI downloads;
2. starts an ephemeral PostgreSQL database and JP-market dispenser;
3. exposes only that temporary dispenser through a Cloudflare Quick Tunnel;
4. waits for the official gplaydl Authenticator to sync the expected account;
5. decrypts the AAS token only in memory and writes GPLAY_EMAIL and
   GPLAY_AAS_TOKEN directly to GitHub Actions repository secrets; and
6. destroys the tunnel, dispenser process and database.

No Google credential is printed, uploaded as an artifact, cached, or persisted
outside GitHub Actions secrets.
"""

from __future__ import annotations

import base64
import json
import os
import re
import secrets
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from nacl.public import PublicKey, SealedBox

from src import local_gplaydl_dispenser as local

POSTGRES_PORT = 5467
DISPENSER_PORT = 18081
TUNNEL_PATTERN = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")


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


def _must_run(
    command: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    label: str,
) -> str:
    result = _run(command, cwd=cwd, env=env)
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
    raise RuntimeError(f"registration dispenser did not become healthy: {last_error}")


def _normalize_email(value: str) -> str:
    email = value.strip().lower()
    if not email or "@" not in email or any(ch.isspace() for ch in email):
        raise RuntimeError("expected_email must be a valid Google account email address")
    return email


def _decrypt_aas_token(encryption_key_hex: str, ciphertext_hex: str) -> str:
    key = bytes.fromhex(encryption_key_hex)
    ciphertext = bytes.fromhex(ciphertext_hex)
    if len(key) != 32:
        raise RuntimeError("registration dispenser encryption key is invalid")
    if len(ciphertext) <= 12:
        raise RuntimeError("registered AAS token ciphertext is invalid")
    token = AESGCM(key).decrypt(ciphertext[:12], ciphertext[12:], None).decode("utf-8")
    if not token.startswith("aas_et/") or len(token) < 32:
        raise RuntimeError("registered Google credential is not a valid AAS token")
    return token


def _github_request(
    method: str,
    path: str,
    admin_token: str,
    payload: dict[str, str] | None = None,
) -> dict:
    url = f"https://api.github.com{path}"
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {admin_token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "Morphe-AutoBuilds-gplay-registration",
            **({"Content-Type": "application/json"} if data is not None else {}),
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read()
            return json.loads(body.decode("utf-8")) if body else {}
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(
            f"GitHub secret API returned HTTP {error.code}: {body}. "
            "Ensure the PAT repository secret can write Actions secrets."
        ) from error


def _set_repository_secret(
    repository: str,
    name: str,
    value: str,
    admin_token: str,
    public_key: dict,
) -> None:
    key_id = str(public_key.get("key_id", "")).strip()
    encoded_key = str(public_key.get("key", "")).strip()
    if not key_id or not encoded_key:
        raise RuntimeError("GitHub Actions secrets public-key response was incomplete")

    sealed_box = SealedBox(PublicKey(base64.b64decode(encoded_key)))
    encrypted_value = base64.b64encode(sealed_box.encrypt(value.encode("utf-8"))).decode("ascii")
    _github_request(
        "PUT",
        f"/repos/{repository}/actions/secrets/{name}",
        admin_token,
        {"encrypted_value": encrypted_value, "key_id": key_id},
    )


def _query_registered_account(container: str, expected_email: str) -> tuple[str, str] | None:
    sql = (
        "SELECT email::text || E'\\t' || encode(aas_token_enc, 'hex') "
        "FROM accounts "
        "WHERE lower(email::text) = lower(:'expected_email') "
        "ORDER BY updated_at DESC LIMIT 1;"
    )
    result = _run(
        [
            "docker",
            "exec",
            container,
            "psql",
            "-qAtX",
            "-U",
            "dispenser",
            "-d",
            "dispenser",
            "-v",
            f"expected_email={expected_email}",
            "-c",
            sql,
        ]
    )
    if result.returncode != 0:
        return None
    line = (result.stdout or "").strip()
    if not line or "\t" not in line:
        return None
    email, ciphertext_hex = line.split("\t", 1)
    return email.strip(), ciphertext_hex.strip()


def _start_tunnel(local_url: str, log_path: Path) -> tuple[subprocess.Popen[str], object, str]:
    cloudflared = shutil.which("cloudflared")
    if not cloudflared:
        raise RuntimeError("cloudflared is required for Google Play account registration")

    log_handle = log_path.open("w", encoding="utf-8")
    process = subprocess.Popen(
        [cloudflared, "tunnel", "--no-autoupdate", "--url", local_url],
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        text=True,
    )
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if process.poll() is not None:
            break
        log_handle.flush()
        text = log_path.read_text(encoding="utf-8", errors="replace")
        match = TUNNEL_PATTERN.search(text)
        if match:
            return process, log_handle, match.group(0)
        time.sleep(0.5)

    tail = "\n".join(log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-30:])
    process.terminate()
    log_handle.close()
    raise RuntimeError(f"Cloudflare Quick Tunnel did not become ready: {tail}")


def _stop_process(process: subprocess.Popen[str] | None) -> None:
    if process is None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def _summary(text: str) -> None:
    path = os.getenv("GITHUB_STEP_SUMMARY", "").strip()
    if not path:
        return
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(text.rstrip() + "\n")


def main() -> int:
    expected_email = _normalize_email(os.getenv("EXPECTED_GPLAY_EMAIL", ""))
    admin_token = os.getenv("GPLAY_SECRET_ADMIN_TOKEN", "").strip()
    repository = os.getenv("GITHUB_REPOSITORY", "").strip()
    wait_minutes = int(os.getenv("GPLAYDL_REGISTRATION_WAIT_MINUTES", "15"))
    if wait_minutes < 1 or wait_minutes > 30:
        raise RuntimeError("registration wait must be between 1 and 30 minutes")
    if not admin_token:
        raise RuntimeError(
            "PAT repository secret is required and must be able to write Actions secrets"
        )
    if not repository or "/" not in repository:
        raise RuntimeError("GITHUB_REPOSITORY is missing")

    for executable in ("git", "go", "docker", "cloudflared"):
        if not shutil.which(executable):
            raise RuntimeError(f"{executable} is required for Google Play account registration")

    # Verify secret-write authorization before asking the user to sign in.
    public_key = _github_request(
        "GET", f"/repos/{repository}/actions/secrets/public-key", admin_token
    )

    postgres_name = f"gplaydl-register-db-{os.getpid()}"
    dispenser_process: subprocess.Popen[str] | None = None
    tunnel_process: subprocess.Popen[str] | None = None
    tunnel_handle = None
    dispenser_handle = None

    with tempfile.TemporaryDirectory(prefix="gplaydl-register-") as directory:
        root = Path(directory)
        checkout = root / "upstream"
        upstream_ref = (
            os.getenv("GPLAYDL_DISPENSER_REF", local.DEFAULT_UPSTREAM_REF).strip()
            or local.DEFAULT_UPSTREAM_REF
        )
        repository_url = (
            os.getenv("GPLAYDL_DISPENSER_REPOSITORY", local.UPSTREAM_REPOSITORY).strip()
            or local.UPSTREAM_REPOSITORY
        )
        postgres_password = secrets.token_hex(16)
        encryption_key_hex = secrets.token_hex(32)

        try:
            print(
                f"Building current gplaydl-dispenser ({upstream_ref}) for one-time registration...",
                flush=True,
            )
            _must_run(
                [
                    "git",
                    "clone",
                    "--depth",
                    "1",
                    "--branch",
                    upstream_ref,
                    repository_url,
                    str(checkout),
                ],
                label="clone upstream gplaydl-dispenser",
            )
            local.patch_upstream_market(checkout)

            dist = checkout / "web" / "dist"
            dist.mkdir(parents=True, exist_ok=True)
            (dist / "index.html").write_text(
                "<!doctype html><title>Morphe registration dispenser</title>\n",
                encoding="utf-8",
            )
            binary = root / "gplaydl-dispenser"
            _must_run(
                ["go", "build", "-o", str(binary), "./cmd/dispenser"],
                cwd=checkout,
                label="build upstream gplaydl-dispenser",
            )

            _must_run(
                [
                    "docker",
                    "run",
                    "--rm",
                    "-d",
                    "--name",
                    postgres_name,
                    "-e",
                    "POSTGRES_USER=dispenser",
                    "-e",
                    f"POSTGRES_PASSWORD={postgres_password}",
                    "-e",
                    "POSTGRES_DB=dispenser",
                    "-p",
                    f"127.0.0.1:{POSTGRES_PORT}:5432",
                    local.POSTGRES_IMAGE,
                ],
                label="start registration Postgres",
            )
            deadline = time.monotonic() + 45
            while time.monotonic() < deadline:
                ready = _run(
                    ["docker", "exec", postgres_name, "pg_isready", "-U", "dispenser"]
                )
                if ready.returncode == 0:
                    break
                time.sleep(0.5)
            else:
                raise RuntimeError("registration Postgres did not become ready")

            local_url = f"http://127.0.0.1:{DISPENSER_PORT}"
            tunnel_log = root / "cloudflared.log"
            tunnel_process, tunnel_handle, public_url = _start_tunnel(local_url, tunnel_log)

            server_env = os.environ.copy()
            server_env.update(
                {
                    "DISPENSER_ADDR": f"127.0.0.1:{DISPENSER_PORT}",
                    "DATABASE_URL": (
                        f"postgres://dispenser:{postgres_password}@127.0.0.1:{POSTGRES_PORT}/"
                        "dispenser?sslmode=disable"
                    ),
                    "DISPENSER_ENCRYPTION_KEY": encryption_key_hex,
                    "DISPENSER_DEV": "1",
                    "PUBLIC_URL": public_url,
                    "RESOURCES_DIR": str((checkout / "resources").resolve()),
                    "GPLAY_DEVICE_COUNTRY": os.getenv("GPLAY_DEVICE_COUNTRY", "JP"),
                    "GPLAY_MCCMNC": os.getenv("GPLAY_MCCMNC", "44010"),
                    "GPLAY_DEFAULT_LOCALE": os.getenv("GPLAY_DEFAULT_LOCALE", "ja_JP"),
                    "GPLAY_TIMEZONE": os.getenv("GPLAY_TIMEZONE", "Asia/Tokyo"),
                }
            )
            dispenser_log = root / "dispenser.log"
            dispenser_handle = dispenser_log.open("w", encoding="utf-8")
            dispenser_process = subprocess.Popen(
                [str(binary)],
                cwd=checkout,
                env=server_env,
                stdout=dispenser_handle,
                stderr=subprocess.STDOUT,
                text=True,
            )
            _wait_http(local_url + "/api/health")

            print("=" * 72, flush=True)
            print(f"TEMPORARY AUTHENTICATOR SERVER: {public_url}", flush=True)
            print(f"EXPECTED GOOGLE ACCOUNT: {expected_email}", flush=True)
            print("=" * 72, flush=True)
            print(
                "On the phone: official gplaydl Authenticator → Settings → "
                "Advanced server settings → Dispenser URL → paste the temporary URL → "
                "Change server → add the expected Google account.",
                flush=True,
            )
            print(
                f"::notice title=Google Play registration::Temporary Authenticator server: {public_url}",
                flush=True,
            )
            _summary(
                f"""
## Google Play account registration

Temporary official-Authenticator dispenser:

`{public_url}`

Expected Google account: `{expected_email}`

On the phone:

1. Open the official **gplaydl Authenticator**.
2. Open **Settings → Advanced server settings**.
3. Set **Dispenser URL** to the temporary URL above and choose **Change server**.
4. Add `{expected_email}`.
5. Keep this workflow open until it reports that `GPLAY_EMAIL` and `GPLAY_AAS_TOKEN` were saved.

The temporary server accepts registration only for this workflow lifetime. The AAS token is never printed, cached or uploaded as an artifact.
"""
            )

            deadline = time.monotonic() + wait_minutes * 60
            row: tuple[str, str] | None = None
            while time.monotonic() < deadline:
                if dispenser_process.poll() is not None:
                    raise RuntimeError("registration dispenser exited unexpectedly")
                row = _query_registered_account(postgres_name, expected_email)
                if row is not None:
                    break
                time.sleep(2)
            if row is None:
                raise RuntimeError(
                    f"timed out after {wait_minutes} minutes waiting for {expected_email}; rerun when ready"
                )

            # Close the public ingress immediately after the expected account arrives.
            _stop_process(tunnel_process)
            tunnel_process = None
            if tunnel_handle is not None:
                tunnel_handle.close()
                tunnel_handle = None

            synced_email, ciphertext_hex = row
            synced_email = _normalize_email(synced_email)
            if synced_email != expected_email:
                raise RuntimeError("the synchronized Google account did not match expected_email")
            aas_token = _decrypt_aas_token(encryption_key_hex, ciphertext_hex)

            # Mask defensively even though the values are never intentionally printed.
            print(f"::add-mask::{synced_email}", flush=True)
            print(f"::add-mask::{aas_token}", flush=True)

            _set_repository_secret(
                repository, "GPLAY_EMAIL", synced_email, admin_token, public_key
            )
            _set_repository_secret(
                repository, "GPLAY_AAS_TOKEN", aas_token, admin_token, public_key
            )

            print(
                "✅ Saved GPLAY_EMAIL and GPLAY_AAS_TOKEN to GitHub Actions repository secrets.",
                flush=True,
            )
            _summary(
                "\n✅ Registration completed. `GPLAY_EMAIL` and `GPLAY_AAS_TOKEN` are now "
                "stored as repository Actions secrets. The next normal build can create its "
                "ephemeral JP-market dispenser directly from those secrets."
            )
            return 0
        finally:
            _stop_process(tunnel_process)
            _stop_process(dispenser_process)
            if tunnel_handle is not None:
                tunnel_handle.close()
            if dispenser_handle is not None:
                dispenser_handle.close()
            _run(["docker", "rm", "-f", postgres_name])


if __name__ == "__main__":
    raise SystemExit(main())
