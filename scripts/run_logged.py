"""Run a command while streaming combined stdout/stderr to a durable log file."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path
from typing import Iterable, TextIO


def _run_once(
    command: list[str],
    env: dict[str, str],
    log: TextIO,
) -> tuple[int, str]:
    captured: list[str] = []
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
        captured.append(line)
    return process.wait(), "".join(captured)


def _write_marker(log: TextIO, message: str) -> None:
    line = f"\n{message}\n"
    sys.stdout.write(line)
    sys.stdout.flush()
    log.write(line)
    log.flush()


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
        return_code, first_output = _run_once(command, env, log)

        # Import only when this wrapper is actually running the repository build
        # command, keeping unrelated uses lightweight.
        source = env.get("SOURCE", "")
        is_morphe_build = source == "morphe"
        morphe_toolchain_fallback = None
        if is_morphe_build:
            from scripts import morphe_toolchain_fallback as fallback_module

            if fallback_module.is_morphe_build_command(command):
                morphe_toolchain_fallback = fallback_module
            else:
                is_morphe_build = False

        if return_code == 0:
            if morphe_toolchain_fallback is not None:
                # Capture the exact latest-first pair that really succeeded.
                # successful-state persistence can then advance the safety
                # anchor without racing a newer upstream release published
                # after this matrix job completed.
                morphe_toolchain_fallback.annotate_primary_success()
            return 0

        # The fallback hook is intentionally outside src.__main__: the normal
        # build path remains latest-first and source-agnostic. Only the exact
        # Morphe resource rebuild regression observed in Run #556 reaches this
        # retry path; other build failures keep their original non-zero result.
        if morphe_toolchain_fallback is None or not morphe_toolchain_fallback.should_retry(
            source, command, first_output
        ):
            return return_code

        reason = (
            "primary Morphe toolchain hit the known XmlEncodeException / "
            "Unexpected array value resource rebuild regression"
        )
        _write_marker(
            log,
            "⚠️  Detected known Morphe upstream resource-toolchain regression; "
            "retrying once with last-known-good toolchain.",
        )
        try:
            metadata = morphe_toolchain_fallback.activate_known_good_toolchain()
        except Exception as error:
            _write_marker(
                log,
                "❌ Could not prepare last-known-good Morphe toolchain: "
                f"{type(error).__name__}: {error}",
            )
            return return_code

        _write_marker(
            log,
            "↩️  Morphe fallback toolchain: "
            f"CLI={metadata.get('cli_tag')} patches={metadata.get('patch_tag')}",
        )
        retry_code, _ = _run_once(command, env, log)
        morphe_toolchain_fallback.annotate_build_report(
            metadata,
            reason=reason,
            retry_succeeded=retry_code == 0,
        )
        if retry_code == 0:
            _write_marker(
                log,
                "✅ Last-known-good Morphe toolchain recovered the build; "
                "latest remains the primary candidate for future runs.",
            )
        else:
            _write_marker(
                log,
                "❌ Last-known-good Morphe retry also failed; preserving the "
                "failure for normal reporting.",
            )
        return retry_code


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
