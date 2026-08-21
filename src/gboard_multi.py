"""Gboard-only Morphe multi-source patch integration.

Gboard is intentionally exceptional: the original request combines Jason's
Gboard bundle with selected patches from Adobo and Morning-Entree on the same
APK.  Normal apps should continue to use a single upstream bundle and its
current defaults.

The adapter works at the Morphe command boundary so the existing APK download,
validation, reporting and signing pipeline stays unchanged.  It asks the
*actual cached bundles* to generate an options file, keeps Jason's current
upstream defaults, and turns the two supplemental bundles into explicit
allowlists from my-patch-config.json.  This avoids maintaining a copied list of
Jason defaults and prevents generic/default patches from supplemental bundles
from leaking into Gboard.
"""

from __future__ import annotations

import atexit
import json
import logging
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Callable, Sequence

GBOARD_PACKAGE = "com.google.android.inputmethod.latin"
PRIMARY_SOURCE = "jason"
EXTRA_SOURCES = ("adobo", "morning-entree")

_TEMP_FILES: list[Path] = []


def _cleanup_temp_files() -> None:
    for path in _TEMP_FILES:
        path.unlink(missing_ok=True)


atexit.register(_cleanup_temp_files)


def _find_bundle(source: str) -> Path:
    root = Path("tools") / source
    candidates = sorted(
        (path for path in root.glob("*.mpp") if path.is_file()),
        key=lambda path: (path.stat().st_mtime_ns, path.name),
        reverse=True,
    )
    if not candidates:
        raise RuntimeError(
            f"Gboard multi-source build requires an .mpp bundle in {root}"
        )
    return candidates[0]


def _explicit_selection(source: str) -> set[str]:
    data = json.loads(Path("my-patch-config.json").read_text(encoding="utf-8"))
    for entry in data.get("patch_list", []):
        if entry.get("app_name") == "gboard" and entry.get("source") == source:
            return {
                str(name)
                for name in (entry.get("force_enable") or [])
                if str(name).strip()
            }
    raise RuntimeError(f"Missing Gboard patch config for supplemental source {source}")


def _bundle_entry(options: list[dict], bundle: Path) -> dict:
    for entry in options:
        source = str((entry.get("meta") or {}).get("source") or "")
        if Path(source).name == bundle.name:
            return entry
    raise RuntimeError(
        f"Morphe options-create did not produce an entry for {bundle.name}"
    )


def _restrict_bundle(entry: dict, requested: set[str], source: str) -> None:
    patches = entry.get("patches")
    if not isinstance(patches, dict):
        raise RuntimeError(f"Invalid Morphe options JSON for {source}: patches missing")

    by_casefold = {name.casefold(): name for name in patches}
    missing = sorted(name for name in requested if name.casefold() not in by_casefold)
    if missing:
        raise RuntimeError(
            f"Gboard {source} requested patch(es) no longer exist: {', '.join(missing)}"
        )

    # Supplemental bundles are exceptions to the normal upstream-default rule:
    # explicitly turn every compatible/universal patch off, then enable only
    # the conflict-reviewed additions recorded in my-patch-config.json.
    for value in patches.values():
        if isinstance(value, dict):
            value["enabled"] = False

    for requested_name in requested:
        canonical = by_casefold[requested_name.casefold()]
        value = patches[canonical]
        if not isinstance(value, dict):
            raise RuntimeError(
                f"Invalid Morphe options JSON for {source}/{canonical}"
            )
        value["enabled"] = True


def _create_options_file(
    command: Sequence[str], primary_bundle: Path, extra_bundles: dict[str, Path]
) -> Path:
    try:
        jar_index = list(command).index("-jar")
    except ValueError as error:
        raise RuntimeError("Could not locate Morphe CLI -jar argument") from error
    if jar_index + 1 >= len(command):
        raise RuntimeError("Morphe CLI jar path is missing")

    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix="-gboard-options.json", delete=False, encoding="utf-8"
    )
    tmp.close()
    options_path = Path(tmp.name)
    _TEMP_FILES.append(options_path)

    java_prefix = list(command[: jar_index + 2])
    options_cmd = [
        *java_prefix,
        "options-create",
        "-p",
        str(primary_bundle),
    ]
    for source in EXTRA_SOURCES:
        options_cmd.extend(["-p", str(extra_bundles[source])])
    options_cmd.extend(
        ["--filter-package-name", GBOARD_PACKAGE, "--out", str(options_path)]
    )

    logging.info(
        "Gboard multi-source: generating defaults from actual Jason/Adobo/Morning bundles"
    )
    result = subprocess.run(options_cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        detail = "\n".join(part.strip() for part in (result.stdout, result.stderr) if part.strip())
        raise RuntimeError(
            "Morphe options-create failed for Gboard multi-source build"
            + (f": {detail[-2000:]}" if detail else "")
        )

    try:
        options = json.loads(options_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError("Morphe produced invalid Gboard options JSON") from error
    if not isinstance(options, list):
        raise RuntimeError("Morphe Gboard options JSON must be an array")

    # Jason is deliberately untouched: its current bundle defaults are the
    # authoritative Gboard baseline.  Only the supplemental bundles are
    # restricted to the non-overlapping additions selected in config.
    _bundle_entry(options, primary_bundle)
    for source in EXTRA_SOURCES:
        entry = _bundle_entry(options, extra_bundles[source])
        _restrict_bundle(entry, _explicit_selection(source), source)

    options_path.write_text(
        json.dumps(options, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return options_path


def prepare_morphe_command(command: Sequence[str]) -> list[str]:
    """Return a Gboard multi-source command, or the original command unchanged."""
    cmd = list(command)
    if os.getenv("APP_NAME") != "gboard" or os.getenv("SOURCE") != PRIMARY_SOURCE:
        return cmd
    if "patch" not in cmd:
        return cmd
    if "--options-file" in cmd:
        raise RuntimeError(
            "Gboard multi-source adapter cannot merge an existing --options-file"
        )

    try:
        primary_index = cmd.index("-p")
    except ValueError as error:
        raise RuntimeError(
            "Gboard multi-source requires the current Morphe nested -p syntax"
        ) from error
    if primary_index + 1 >= len(cmd):
        raise RuntimeError("Gboard primary patch bundle is missing")

    primary_bundle = Path(cmd[primary_index + 1])
    extra_bundles = {source: _find_bundle(source) for source in EXTRA_SOURCES}
    options_path = _create_options_file(cmd, primary_bundle, extra_bundles)

    # --exclusive is global across all bundles.  The generated options file
    # already contains per-bundle enabled states, so keeping --exclusive would
    # incorrectly erase Jason's default set.
    cmd = [arg for arg in cmd if arg != "--exclusive"]
    primary_index = cmd.index("-p")
    insert_at = primary_index + 2
    extra_args: list[str] = []
    for source in EXTRA_SOURCES:
        extra_args.extend(["-p", str(extra_bundles[source])])
    cmd[insert_at:insert_at] = extra_args

    # Global option: place it immediately before the APK positional argument.
    cmd[-1:-1] = ["--options-file", str(options_path)]
    logging.info(
        "Gboard multi-source enabled: Jason defaults + Adobo %s + Morning-Entree %s",
        sorted(_explicit_selection("adobo")),
        sorted(_explicit_selection("morning-entree")),
    )
    return cmd


def install() -> None:
    """Install the narrowly scoped command adapter once per Python process."""
    from src import utils

    original: Callable = utils.run_process
    if getattr(original, "_gboard_multi_installed", False):
        return

    def wrapped(command, *args, **kwargs):
        return original(prepare_morphe_command(command), *args, **kwargs)

    wrapped._gboard_multi_installed = True  # type: ignore[attr-defined]
    utils.run_process = wrapped
