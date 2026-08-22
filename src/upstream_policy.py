"""Runtime policy derived from the current upstream patch bundle.

The repository may keep local patch options and provider fallback metadata, but
those values must never override the patch bundle's current recommendations.
This module prepares an ephemeral CI working copy before download/patch code is
loaded:

* only options/required entries for upstream-recommended patches remain active;
* ``force_enable`` is ignored so it cannot promote a non-recommended patch;
* legacy per-app patch allowlists are ignored when recommendation metadata is
  unavailable, leaving selection to the CLI defaults;
* when the patch side has no version restriction (``any``), provider version
  pins are ignored for this run so Google Play's current release is selected.

Nothing here changes committed configuration. GitHub Actions starts from a clean
checkout for every job, so these edits affect only the current process/worktree.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Iterable


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


def _patch_entries(source: str, tools_root: Path = Path("tools")) -> list[dict] | None:
    path = tools_root / source / "patches-list.json"
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        logging.warning("Could not read %s for upstream policy: %s", path, error)
        return None
    patches = raw.get("patches") if isinstance(raw, dict) else raw
    if not isinstance(patches, list):
        return None
    return [patch for patch in patches if isinstance(patch, dict)]


def _compatible_packages(value: object) -> set[str]:
    if isinstance(value, dict):
        return {str(name) for name in value}
    if isinstance(value, list):
        packages: set[str] = set()
        for item in value:
            if not isinstance(item, dict):
                continue
            name = item.get("packageName", item.get("name"))
            if name:
                packages.add(str(name))
        return packages
    return set()


def recommended_patch_names(entries: Iterable[dict], package: str) -> set[str]:
    """Return patches upstream currently recommends for ``package``."""
    recommended: set[str] = set()
    for patch in entries:
        name = str(patch.get("name") or "").strip()
        if not name:
            continue
        compatible = patch.get("compatiblePackages") or []
        packages = _compatible_packages(compatible)
        if compatible and package not in packages:
            continue
        if bool(patch.get("use", patch.get("default", True))):
            recommended.add(name)
    return recommended


def _sanitize_patch_config(
    app_name: str,
    source: str,
    recommended: set[str] | None,
    config_path: Path = Path("my-patch-config.json"),
) -> None:
    if not config_path.is_file():
        return
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        logging.warning("Could not prepare upstream patch policy: %s", error)
        return

    changed = False
    for entry in raw.get("patch_list", []):
        if not isinstance(entry, dict):
            continue
        if entry.get("app_name") != app_name or entry.get("source") != source:
            continue

        if entry.get("force_enable"):
            entry["force_enable"] = []
            changed = True

        # When recommendation metadata exists, options/required are meaningful
        # only for patches upstream recommends right now. When it does not
        # exist, suppress them and let the patch CLI defaults decide selection.
        allowed = recommended or set()
        options = entry.get("options") or []
        filtered_options = [
            option for option in options
            if isinstance(option, dict) and option.get("patch") in allowed
        ]
        if filtered_options != options:
            entry["options"] = filtered_options
            changed = True

        for key in ("required", "required_patches"):
            if key not in entry:
                continue
            values = entry.get(key) or []
            filtered = [name for name in values if name in allowed]
            if filtered != values:
                entry[key] = filtered
                changed = True
        break

    if changed:
        config_path.write_text(
            json.dumps(raw, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    if recommended is None:
        legacy = Path("patches") / f"{app_name}-{source}.txt"
        if legacy.is_file():
            legacy.unlink()
            logging.info(
                "Upstream recommendation metadata unavailable; ignoring legacy patch allowlist %s",
                legacy,
            )


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


def _patch_has_version_restriction(package: str, source: str) -> bool | None:
    """Return False for ``any``, True for explicit versions, None if unknown."""
    cli, bundle = _tool_files(source)
    if cli is None or bundle is None:
        return None
    try:
        # Import lazily: src.__init__ has already initialized shared globals by
        # the time prepare_runtime_policy() calls this helper.
        from src import utils

        candidates = utils.get_supported_version_candidates(
            package,
            str(cli),
            str(bundle),
        )
    except Exception as error:
        logging.info("Could not resolve upstream version policy for %s: %s", package, error)
        return None
    return bool(candidates)


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
    """Apply the upstream-driven policy to the current CI working copy."""
    app_name = os.getenv("APP_NAME", "").strip()
    source = os.getenv("SOURCE", "").strip()
    if not app_name or not source:
        return

    package = _package_for_app(app_name)
    if not package:
        return

    entries = _patch_entries(source)
    recommended = (
        recommended_patch_names(entries, package)
        if entries is not None
        else None
    )
    _sanitize_patch_config(app_name, source, recommended)

    restriction = _patch_has_version_restriction(package, source)
    if restriction is False:
        logging.info(
            "Patch side has no version restriction for %s; ignoring local version pins and using current store release",
            app_name,
        )
        _ignore_provider_version_pins_for_any(app_name)
