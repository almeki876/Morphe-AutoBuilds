"""Resolve patch-source release tags and the shared build-tools cache key."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


SOURCE_SPECS = (
    ("morphe", "MorpheApp/morphe-patches", "MORPHE_TAG"),
    ("anddea", "anddea/revanced-patches", "ANDDEA_TAG"),
    ("rushiranpise", "rushiranpise/morphe-patches", "RUSHIRANPISE_TAG"),
    ("rookie", "RookieEnough/De-Vanced", "ROOKIE_TAG"),
    ("tosox", "Tosox/revanced-patches", "TOSOX_TAG"),
    ("dropped", "indrastorms/Dropped-Patches", "DROPPED_TAG"),
)
OUTPUT_ORDER = ("morphe", "anddea", "rushiranpise", "rookie", "tosox", "yuzu", "dropped")


def resolve_tag(repository: str, hint: str | None) -> str:
    hint = (hint or "").strip()
    if hint and hint != "latest":
        return hint
    result = subprocess.run(
        [
            "gh",
            "api",
            f"repos/{repository}/releases?per_page=100",
            "--jq",
            "sort_by(.published_at) | reverse | .[0].tag_name",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    value = result.stdout.strip() if result.returncode == 0 else ""
    return value or "unknown"


def resolve_all(environ: dict[str, str]) -> dict[str, str]:
    resolved = {
        name: resolve_tag(repository, environ.get(env_name))
        for name, repository, env_name in SOURCE_SPECS
    }
    # This source is branch-backed rather than release-backed.
    resolved["yuzu"] = "patche"
    return resolved


def cache_key(config_hash: str, resolved: dict[str, str]) -> str:
    version_string = "__".join(resolved[name] for name in OUTPUT_ORDER)
    return f"build-tools-{config_hash}-{version_string}"


def write_outputs(path: Path, resolved: dict[str, str], key: str) -> None:
    lines = [f"{name}_resolved={resolved[name]}" for name in OUTPUT_ORDER]
    lines.append(f"cache-key={key}")
    with path.open("a", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")


def main() -> int:
    resolved = resolve_all(dict(os.environ))
    key = cache_key(os.getenv("CONFIG_HASH", ""), resolved)
    output = os.getenv("GITHUB_OUTPUT")
    if not output:
        raise RuntimeError("GITHUB_OUTPUT is required")
    write_outputs(Path(output), resolved, key)

    print("Resolved tags:")
    for name in OUTPUT_ORDER:
        print(f"  {name}: {resolved[name]}")
    print(f"Cache key: {key}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
