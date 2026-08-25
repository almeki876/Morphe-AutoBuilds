"""Resolve every sources/*.json patch tag and the shared tools cache key."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from scripts.check_upstream_sources import iter_sources, resolve_source_tag


def resolve_all(environ: dict[str, str]) -> dict[str, str]:
    raw = environ.get("SOURCE_TAGS_JSON", "").strip()
    supplied = json.loads(raw) if raw else {}
    if not isinstance(supplied, dict):
        raise ValueError("SOURCE_TAGS_JSON must be a JSON object")
    resolved = {}
    for source in iter_sources():
        name = str(source["name"])
        resolved[name] = str(supplied.get(name) or resolve_source_tag(source))
        if not resolved[name]:
            raise RuntimeError(f"could not resolve patch tag for {name}")
    return resolved


def cache_key(config_hash: str, resolved: dict[str, str]) -> str:
    encoded = json.dumps(resolved, sort_keys=True, separators=(",", ":")).encode()
    digest = hashlib.sha256(encoded).hexdigest()[:20]
    return f"build-tools-{config_hash}-{digest}"


def main() -> int:
    resolved = resolve_all(dict(os.environ))
    key = cache_key(os.getenv("CONFIG_HASH", ""), resolved)
    output = os.getenv("GITHUB_OUTPUT")
    if not output:
        raise RuntimeError("GITHUB_OUTPUT is required")
    with Path(output).open("a", encoding="utf-8") as handle:
        handle.write(f"resolved_tags={json.dumps(resolved, separators=(',', ':'))}\n")
        handle.write(f"cache-key={key}\n")

    print("Resolved tags:")
    for name, tag in sorted(resolved.items()):
        print(f"  {name}: {tag}")
    print(f"Cache key: {key}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
