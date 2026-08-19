"""Fail fast when repository configuration is internally inconsistent.

This validator deliberately does not access the network.  It is cheap enough
to run at the beginning of every build and before scheduled upstream checks.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent


ALLOWED_ARCHES = {"universal", "arm64-v8a", "armeabi-v7a"}
KNOWN_PROVIDERS = {
    "github",
    "apkmirror",
    "apkpure",
    "uptodown",
    "softonic",
    "aptoide",
    "apkcombo",
}
PACKAGE_RE = re.compile(r"^[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)+$")


class Validation:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.warnings: list[str] = []

    def error(self, message: str) -> None:
        self.errors.append(message)

    def warn(self, message: str) -> None:
        self.warnings.append(message)


def _load_json(path: Path, validation: Validation) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        validation.error(f"missing required file: {path.relative_to(ROOT)}")
    except (OSError, json.JSONDecodeError) as error:
        validation.error(f"invalid JSON in {path.relative_to(ROOT)}: {error}")
    return None


def _string_list(
    value: Any,
    field: str,
    context: str,
    validation: Validation,
) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        validation.error(f"{context}.{field} must be a list of non-empty strings")
        return []
    if len(value) != len(set(value)):
        validation.error(f"{context}.{field} contains duplicate values")
    return value


def _configured_packages(validation: Validation) -> dict[str, set[str]]:
    result: dict[str, set[str]] = {}
    apps_root = ROOT / "apps"
    if not apps_root.is_dir():
        validation.error("missing required directory: apps")
        return result
    for provider_dir in sorted(apps_root.iterdir()):
        if not provider_dir.is_dir():
            continue
        if provider_dir.name not in KNOWN_PROVIDERS:
            validation.error(f"unknown provider directory: apps/{provider_dir.name}")
            continue
        for path in sorted(provider_dir.glob("*.json")):
            data = _load_json(path, validation)
            if not isinstance(data, dict):
                validation.error(f"{path.relative_to(ROOT)} must be an object")
                continue
            package = data.get("package")
            if not isinstance(package, str) or not package.strip():
                validation.error(f"{path.relative_to(ROOT)}: missing package ID")
                continue
            result.setdefault(path.stem, set()).add(package)
    for app, packages in sorted(result.items()):
        if len(packages) > 1:
            validation.error(
                f"conflicting package IDs for {app}: {', '.join(sorted(packages))}"
            )
    return result


def _validate_patch_config(
    packages: dict[str, set[str]],
    validation: Validation,
) -> set[tuple[str, str]]:
    data = _load_json(ROOT / "my-patch-config.json", validation)
    if not isinstance(data, dict) or not isinstance(data.get("patch_list"), list):
        validation.error("my-patch-config.json.patch_list must be an array")
        return set()

    pairs: set[tuple[str, str]] = set()
    for index, entry in enumerate(data["patch_list"]):
        context = f"my-patch-config.json.patch_list[{index}]"
        if not isinstance(entry, dict):
            validation.error(f"{context} must be an object")
            continue
        app = entry.get("app_name")
        source = entry.get("source")
        if not isinstance(app, str) or not app.strip():
            validation.error(f"{context}.app_name must be a non-empty string")
            continue
        if not isinstance(source, str) or not source.strip():
            validation.error(f"{context}.source must be a non-empty string")
            continue
        pair = (app, source)
        if pair in pairs:
            validation.error(f"duplicate patch target: {app} / {source}")
        pairs.add(pair)

        if not (ROOT / "sources" / f"{source}.json").is_file():
            validation.error(f"{context}: source config does not exist: {source}")
        if not packages.get(app):
            validation.error(f"{context}: no APK package ID configured for {app}")

        for field in ("enabled", "skip_build"):
            if field in entry and not isinstance(entry[field], bool):
                validation.error(f"{context}.{field} must be a boolean")

        options = entry.get("options", [])
        if not isinstance(options, list):
            validation.error(f"{context}.options must be an array")
        else:
            option_keys: set[tuple[str, str]] = set()
            for option_index, option in enumerate(options):
                option_context = f"{context}.options[{option_index}]"
                if not isinstance(option, dict):
                    validation.error(f"{option_context} must be an object")
                    continue
                missing = {"patch", "key", "value"} - option.keys()
                if missing:
                    validation.error(
                        f"{option_context} is missing: {', '.join(sorted(missing))}"
                    )
                    continue
                patch = option["patch"]
                key = option["key"]
                if not isinstance(patch, str) or not patch.strip():
                    validation.error(f"{option_context}.patch must be non-empty")
                if not isinstance(key, str) or not key.strip():
                    validation.error(f"{option_context}.key must be non-empty")
                option_key = (str(patch), str(key))
                if option_key in option_keys:
                    validation.error(
                        f"{context}: duplicate option {option_key[0]} / {option_key[1]}"
                    )
                option_keys.add(option_key)
        _string_list(entry.get("disable"), "disable", context, validation)
        _string_list(entry.get("force_enable"), "force_enable", context, validation)
    return pairs


def _validate_arch_config(
    patch_pairs: set[tuple[str, str]],
    validation: Validation,
) -> None:
    data = _load_json(ROOT / "arch-config.json", validation)
    if not isinstance(data, list):
        validation.error("arch-config.json must be an array")
        return
    seen: set[tuple[str, str]] = set()
    for index, entry in enumerate(data):
        context = f"arch-config.json[{index}]"
        if not isinstance(entry, dict):
            validation.error(f"{context} must be an object")
            continue
        pair = (entry.get("app_name"), entry.get("source"))
        if not all(isinstance(value, str) and value for value in pair):
            validation.error(f"{context} needs non-empty app_name and source")
            continue
        if pair in seen:
            validation.error(f"duplicate architecture target: {pair[0]} / {pair[1]}")
        seen.add(pair)
        if pair not in patch_pairs:
            validation.error(
                f"{context} refers to missing patch target: {pair[0]} / {pair[1]}"
            )
        arches = entry.get("arches", entry.get("arch"))
        if isinstance(arches, str):
            arches = [arches]
        if not isinstance(arches, list) or not arches:
            validation.error(f"{context}.arches must be a non-empty array")
            continue
        unknown = sorted(set(arches) - ALLOWED_ARCHES)
        if unknown:
            validation.error(f"{context} has unsupported arches: {', '.join(unknown)}")


def _validate_provider_configs(validation: Validation) -> None:
    for path in sorted((ROOT / "apps").glob("*/*.json")):
        data = _load_json(path, validation)
        if not isinstance(data, dict):
            continue
        relative = path.relative_to(ROOT)
        package = data.get("package")
        if isinstance(package, str) and not PACKAGE_RE.fullmatch(package):
            validation.error(f"{relative}: invalid Android package ID {package!r}")
        fallback_versions = data.get("fallback_versions")
        if fallback_versions is not None:
            if not isinstance(fallback_versions, list) or not fallback_versions:
                validation.error(f"{relative}: fallback_versions must be a non-empty array")
            else:
                for index, fallback in enumerate(fallback_versions):
                    valid_string = isinstance(fallback, str) and bool(fallback.strip())
                    valid_object = (
                        isinstance(fallback, dict)
                        and isinstance(fallback.get("name"), str)
                        and bool(fallback["name"].strip())
                        and (
                            "code" not in fallback
                            or (
                                isinstance(fallback["code"], (str, int))
                                and str(fallback["code"]).isdigit()
                            )
                        )
                    )
                    if not valid_string and not valid_object:
                        validation.error(
                            f"{relative}.fallback_versions[{index}] must contain a version name"
                        )
        if data.get("exclusive") is True and data.get("primary") is not True:
            validation.error(f"{relative}: exclusive requires primary=true")
        if path.parent.name == "github":
            for field in ("user", "repo"):
                if not isinstance(data.get(field), str) or not data[field].strip():
                    validation.error(f"{relative}: missing GitHub field {field}")
            selectors = ("asset_pattern", "asset_regex", "asset_name")
            if not any(data.get(field) for field in selectors):
                validation.warn(
                    f"{relative}: no asset selector; first matching release asset is fragile"
                )


def _validate_sources(validation: Validation) -> None:
    for path in sorted((ROOT / "sources").glob("*.json")):
        data = _load_json(path, validation)
        if not isinstance(data, list) or not data:
            validation.error(f"{path.relative_to(ROOT)} must be a non-empty array")
            continue
        for index, item in enumerate(data):
            if not isinstance(item, dict):
                validation.error(
                    f"{path.relative_to(ROOT)}[{index}] must be an object"
                )
                continue
            context = f"{path.relative_to(ROOT)}[{index}]"
            if index == 0:
                if not isinstance(item.get("name"), str) or not item["name"].strip():
                    validation.error(f"{context}: source header needs a name")
                continue
            for field in ("user", "repo", "tag"):
                if not isinstance(item.get(field), str) or not item[field].strip():
                    validation.error(f"{context}: missing release field {field}")


def _validate_state(validation: Validation) -> None:
    data = _load_json(ROOT / "last-tags.json", validation)
    if not isinstance(data, dict):
        validation.error("last-tags.json must be an object")
        return
    for key, value in data.items():
        if not isinstance(value, str):
            validation.error(f"last-tags.json.{key} must be a string")
        elif value.lstrip().startswith(("{", "[")):
            validation.error(
                f"last-tags.json.{key} contains an API response instead of a version"
            )


def validate() -> Validation:
    result = Validation()
    packages = _configured_packages(result)
    patch_pairs = _validate_patch_config(packages, result)
    _validate_arch_config(patch_pairs, result)
    _validate_provider_configs(result)
    _validate_sources(result)
    _validate_state(result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true", help="emit machine-readable output")
    args = parser.parse_args()
    result = validate()
    if args.json:
        print(
            json.dumps(
                {"errors": result.errors, "warnings": result.warnings},
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        for warning in result.warnings:
            print(f"WARNING: {warning}")
        for error in result.errors:
            print(f"ERROR: {error}")
        print(
            f"Repository validation: {len(result.errors)} error(s), "
            f"{len(result.warnings)} warning(s)"
        )
    return 1 if result.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
