"""Install checksum-pinned Google Play helper clients for CI jobs."""

from __future__ import annotations

import hashlib
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Client:
    name: str
    url: str
    sha256: str
    version_args: tuple[str, ...]


CLIENTS = (
    Client(
        name="playfetch",
        url="https://github.com/Exmeaning/playfetch/releases/download/v0.9.1/playfetch-v0.9.1-linux-amd64",
        sha256="7c91fa249309ee2e4105b12a7dedcbdba5d8c20d2f978442d59669c3e5350293",
        version_args=("version",),
    ),
    Client(
        name="apkeep",
        url="https://github.com/EFForg/apkeep/releases/download/1.0.0/apkeep-x86_64-unknown-linux-gnu",
        sha256="a23579a3ba366d25a6d69848189b983d65662f4ecf4b9e11e16510811659de4e",
        version_args=("--version",),
    ),
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def install_client(client: Client, bin_dir: Path) -> Path:
    target = bin_dir / client.name
    subprocess.run(["curl", "-fsSL", client.url, "-o", str(target)], check=True)
    actual = sha256_file(target)
    if actual != client.sha256:
        target.unlink(missing_ok=True)
        raise RuntimeError(
            f"checksum mismatch for {client.name}: expected {client.sha256}, got {actual}"
        )
    target.chmod(0o755)
    subprocess.run([str(target), *client.version_args], check=True)
    return target


def main() -> int:
    bin_dir = Path.home() / ".local" / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    for client in CLIENTS:
        install_client(client, bin_dir)

    github_path = os.getenv("GITHUB_PATH")
    if github_path:
        with Path(github_path).open("a", encoding="utf-8") as handle:
            handle.write(str(bin_dir) + "\n")
    else:
        print(f"Add {bin_dir} to PATH")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
