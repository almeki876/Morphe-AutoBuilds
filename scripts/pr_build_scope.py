"""Select the smallest useful APK build scope for a pull request."""

from __future__ import annotations

import argparse
import json
from pathlib import Path, PurePosixPath
import sys


SMOKE_APP = "crunchyroll"
CORE_BUILD_PATHS = frozenset(
    {
        ".github/workflows/build.yml",
        ".github/workflows/check-upstream.yml",
        "arch-config.json",
        "my-patch-config.json",
        "scripts/download_all_tools.py",
        "scripts/download_apks.py",
        "scripts/prepare_matrix.py",
        "scripts/check_upstream_sources.py",
    }
)


def _configured_apps(config_path: Path = Path("my-patch-config.json")) -> set[str]:
    data = json.loads(config_path.read_text(encoding="utf-8"))
    return {
        str(item["app_name"])
        for item in data.get("patch_list", [])
        if isinstance(item, dict) and item.get("app_name")
    }


def _patch_app(stem: str, app_names: set[str]) -> str | None:
    matches = [
        app for app in app_names
        if stem == app or stem.startswith(f"{app}-")
    ]
    return max(matches, key=len, default=None)


def select_scope(
    paths: list[str],
    app_names: set[str] | None = None,
) -> tuple[set[str], set[str]]:
    """Return exact app and patch-source targets affected by changed paths."""
    apps: set[str] = set()
    sources: set[str] = set()
    needs_smoke = False
    configured_apps = app_names if app_names is not None else _configured_apps()

    for raw_path in paths:
        normalized = raw_path.strip().replace("\\", "/").lstrip("./")
        if not normalized:
            continue
        path = PurePosixPath(normalized)
        parts = path.parts

        if len(parts) == 3 and parts[0] == "apps" and path.suffix == ".json":
            apps.add(path.stem)
        elif len(parts) == 2 and parts[0] == "app-metadata" and path.suffix == ".json":
            apps.add(path.stem)
        elif len(parts) == 2 and parts[0] == "sources" and path.suffix == ".json":
            sources.add(path.stem)
        elif len(parts) == 2 and parts[0] == "patches" and path.suffix == ".txt":
            app = _patch_app(path.stem, configured_apps)
            if app:
                apps.add(app)
            else:
                needs_smoke = True
        elif normalized in CORE_BUILD_PATHS or normalized.startswith("src/"):
            needs_smoke = True

    if needs_smoke and not apps and not sources:
        apps.add(SMOKE_APP)
    return apps, sources


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--github-output", type=Path)
    args = parser.parse_args()

    apps, sources = select_scope(sys.stdin.read().splitlines())
    values = {
        "needs_build": "true" if apps or sources else "false",
        "updated_apps": ",".join(sorted(apps)),
        "updated_sources": ",".join(sorted(sources)),
    }
    output = "".join(f"{key}={value}\n" for key, value in values.items())
    if args.github_output:
        with args.github_output.open("a", encoding="utf-8") as handle:
            handle.write(output)
    else:
        print(output, end="")


if __name__ == "__main__":
    main()
