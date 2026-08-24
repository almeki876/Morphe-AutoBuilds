"""Run a command while streaming combined stdout/stderr to a durable log file."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path
from typing import Iterable


def run_logged(
    command: list[str],
    output: Path,
    *,
    unset_env: Iterable[str] = (),
) -> int:
    if not command:
        raise ValueError("command must not be empty")
    env = dict(os.environ)
    for name in unset_env:
        env.pop(name, None)

    with output.open("w", encoding="utf-8") as log:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=env,
        )
        assert process.stdout is not None
        for line in process.stdout:
            sys.stdout.write(line)
            sys.stdout.flush()
            log.write(line)
            log.flush()
        return process.wait()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--unset-env", action="append", default=[])
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = list(args.command)
    if command and command[0] == "--":
        command.pop(0)
    return run_logged(command, args.output, unset_env=args.unset_env)


if __name__ == "__main__":
    raise SystemExit(main())
