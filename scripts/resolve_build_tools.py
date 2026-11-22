"""Resolve every sources/*.json patch tag and the shared tools cache key."""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.check_upstream_sources import iter_sources, resolve_source_tag

STATE_FILE = Path("last-tags.json")


def _read_last_tags() -> dict[str, str]:
    if not STATE_FILE.is_file():
        return {}
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        return {k: str(v) for k, v in data.items() if isinstance(v, str)}
    except Exception:
        return {}


def resolve_all(environ: dict[str, str]) -> dict[str, str]:
    raw = environ.get("SOURCE_TAGS_JSON", "").strip()
    supplied = json.loads(raw) if raw else {}
    if not isinstance(supplied, dict):
        raise ValueError("SOURCE_TAGS_JSON must be a JSON object")
    last_tags = _read_last_tags()
    cached_bundle_tags: dict[str, str] | None = None
    resolved = {}
    unresolved_sources = {}
    for source in iter_sources():
        name = str(source["name"])
        try:
            tag = str(supplied.get(name) or resolve_source_tag(source)).strip()
        except Exception as error:
            if cached_bundle_tags is None:
                try:
                    from src import patch_bundle_cache
                    cached_bundle_tags = patch_bundle_cache.get_cached_bundle_tags()
                except Exception:
                    cached_bundle_tags = {}
            cached_tag = str(cached_bundle_tags.get(name) or "").strip()
            if cached_tag:
                print(
                    f"INFO: source '{name}' upstream resolution failed ({error}); "
                    f"using cached tag '{cached_tag}' from draft release",
                    file=sys.stderr,
                )
                tag = cached_tag
            else:
                print(
                    f"WARNING: skipping source '{name}': upstream release could not be resolved ({error}) and no cached bundle exists in draft release",
                    file=sys.stderr,
                )
                unresolved_sources[name] = {
                    "source": name,
                    "reasons": [f"Upstream release could not be resolved: {error}"],
                }
                continue
        if not tag:
            print(
                f"WARNING: skipping source '{name}': no release tag found",
                file=sys.stderr,
            )
            unresolved_sources[name] = {
                "source": name,
                "reasons": ["No release tag found"],
            }
            continue
        resolved[name] = tag

    if unresolved_sources:
        metadata_dir = Path("build-metadata")
        metadata_dir.mkdir(parents=True, exist_ok=True)
        failures_file = metadata_dir / "tool-download-failures.json"
        existing = {}
        if failures_file.is_file():
            try:
                existing = json.loads(failures_file.read_text(encoding="utf-8"))
            except Exception:
                existing = {}
        existing.update(unresolved_sources)
        failures_file.write_text(json.dumps(existing, indent=2), encoding="utf-8")

    if not resolved:
        raise RuntimeError("could not resolve patch tag for any source")
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
