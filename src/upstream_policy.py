"""Runtime policy for upstream APK version compatibility.

This module must not mutate patch selection from ``my-patch-config.json``.
Explicit ``options``, ``disable``, ``force_enable`` and ``required`` entries are
repository-owned build intent and are consumed unchanged by the patch builder.

The only runtime adjustment kept here concerns provider version pins: when the
current upstream patch bundle explicitly reports that an app supports ``any``
(or ``null``) version, stale provider version pins are removed from the ephemeral
CI working copy so the current store release can be selected.

Nothing here changes committed configuration. GitHub Actions starts from a clean
checkout for every job, so provider edits affect only the current process/worktree.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path


def _tool_files(source: str, tools_root: Path = Path("tools")) -> tuple[Path | None, Path | None]:
    root = tools_root / source
    if not root.is_dir():
        return None, None

    files = [path for path in root.iterdir() if path.is_file()]
    bundles = [
        path for path in files
        if path.suffix.lower() in {".mpp", ".rvp"}
        or (path.suffix.lower() == ".jar" and "patch" in path.name.lower())
    ]
    cli_candidates = [
        path for path in files
        if path.suffix.lower() == ".jar" and "cli" in path.name.lower()
    ]
    if not cli_candidates:
        non_patch_jars = [
            path for path in files
            if path.suffix.lower() == ".jar" and path not in bundles
        ]
        cli_candidates = non_patch_jars

    cli = max(cli_candidates, key=lambda path: path.stat().st_mtime, default=None)
    bundle = max(bundles, key=lambda path: path.stat().st_mtime, default=None)
    return cli, bundle


def _package_for_app(app_name: str, apps_root: Path = Path("apps")) -> str | None:
    packages: set[str] = set()
    if not apps_root.is_dir():
        return None
    for config_path in apps_root.glob(f"*/{app_name}.json"):
        try:
            raw = json.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        package = raw.get("package") if isinstance(raw, dict) else None
        if package:
            packages.add(str(package))
    return next(iter(packages)) if len(packages) == 1 else None


def _list_versions_output(package: str, cli: Path, bundle: Path) -> str | None:
    """Ask the CLI so ``any``/``null`` are distinguishable from errors."""
    try:
        from src import cli_compat, utils

        kind = cli_compat.detect_cli_kind(cli)
        if kind == cli_compat.MORPHE:
            command = [
                "java", "-jar", str(cli), "list-versions",
                "--patches", str(bundle), "-f", package,
            ]
        elif kind == cli_compat.REVANCED_V5PLUS:
            command = [
                "java", "-jar", str(cli), "list-versions",
                str(bundle), "-f", package,
            ]
        else:
            command = [
                "java", "-jar", str(cli), "list-versions",
                "-f", package, str(bundle),
            ]
        return utils.run_process(
            command,
            capture=True,
            silent=True,
            check=False,
        )
    except Exception as error:
        logging.info("Could not inspect raw list-versions output for %s: %s", package, error)
        return None


def _patch_has_version_restriction(package: str, source: str) -> bool | None:
    """Return False for ``any``/``null``, True for versions, else None."""
    cli, bundle = _tool_files(source)
    if cli is None or bundle is None:
        return None
    try:
        from src import utils

        candidates = utils.get_supported_version_candidates(
            package,
            str(cli),
            str(bundle),
        )
    except Exception as error:
        logging.info("Could not resolve upstream version policy for %s: %s", package, error)
        return None
    if candidates:
        return True

    output = _list_versions_output(package, cli, bundle)
    if not output:
        return None
    if any(
        line.strip().casefold() in {"any", "null"}
        for line in output.splitlines()
    ):
        return False
    return None


def _ignore_provider_version_pins_for_any(app_name: str, apps_root: Path = Path("apps")) -> None:
    """Remove version/versionCode pins only from this ephemeral working copy."""
    if not apps_root.is_dir():
        return
    for config_path in apps_root.glob(f"*/{app_name}.json"):
        try:
            raw = json.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(raw, dict):
            continue
        changed = False
        for key in ("version", "version_code"):
            if key in raw:
                raw.pop(key, None)
                changed = True
        if changed:
            config_path.write_text(
                json.dumps(raw, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )


def prepare_runtime_policy() -> None:
    """Apply only upstream APK-version behavior to the current CI worktree."""
    app_name = os.getenv("APP_NAME", "").strip()
    source = os.getenv("SOURCE", "").strip()
    if not app_name or not source:
        return

    package = _package_for_app(app_name)
    if not package:
        return

    restriction = _patch_has_version_restriction(package, source)
    if restriction is False:
        _ignore_provider_version_pins_for_any(app_name)
