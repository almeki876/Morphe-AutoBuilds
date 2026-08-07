"""Download and validate the APK version recommended by each patch CLI.

The matrix file is produced from live ``list-versions`` output. Entries with
no fixed compatible version (``Any``) resolve the current version from the
normal provider priority. Successful app/version downloads are reused across
patch sources, while every app/source pair remains present in the report.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.probe_apk_sources import _resolve
from src import apk_cache, providers, utils
from src.downloads import normalize_download
from src.downloader import download_resource
from src.versioning import VersionCandidate, parse_candidate


def _safe(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-") or "unknown"


def _candidate_from_row(row: dict) -> tuple[VersionCandidate, str]:
    candidates = row.get("candidates") or []
    if candidates:
        first = candidates[0]
        return (
            VersionCandidate(
                name=first["name"],
                code=first.get("code"),
                raw=first.get("display"),
            ),
            "cli-fixed",
        )

    errors: list[str] = []
    for provider in providers.download_priority(row["app"]):
        try:
            config = providers.load_config(row["app"], provider)
            if config is None:
                continue
            value = providers.MODULES[provider].get_latest_version(
                row["app"], config
            )
            if value:
                candidate = parse_candidate(str(value))
                return candidate or VersionCandidate(name=str(value)), "cli-any"
        except Exception as error:
            errors.append(
                f"{provider}: {type(error).__name__}: "
                f"{utils.safe_text_for_log(error)}"
            )
    raise RuntimeError("; ".join(errors) or "no provider returned a latest version")


def _download(
    row: dict,
    candidate: VersionCandidate,
    output_dir: Path,
) -> dict:
    attempts: list[dict] = []
    for provider in providers.download_priority(row["app"]):
        attempt = {"provider": provider, "status": "failed"}
        try:
            config = providers.load_config(row["app"], provider)
            if config is None:
                attempt.update(status="skipped", error="no configuration")
                attempts.append(attempt)
                continue
            config["arch"] = "arm64-v8a"
            spec = normalize_download(
                _resolve(provider, row["app"], config, candidate)
            )
            referer = spec.headers.get("Referer") or providers.referer(
                provider, row["app"], config
            )
            target = output_dir / (
                f"{_safe(row['app'])}-{_safe(candidate.canonical)}-"
                f"{_safe(provider)}.apk"
            )
            if target.is_file() and apk_cache.is_valid_apk_archive(target):
                attempt.update(
                    status="ok",
                    bytes=target.stat().st_size,
                    file=str(target),
                    reused=True,
                )
                attempts.append(attempt)
                return {"provider": provider, "attempts": attempts, **attempt}
            path = download_resource(
                spec.url,
                name=str(target),
                referer=None if "Referer" in spec.headers else referer,
                headers=spec.headers,
            )
            if not apk_cache.is_valid_apk_archive(path):
                raise ValueError("downloaded file is not a valid APK archive")
            attempt.update(
                status="ok",
                bytes=path.stat().st_size,
                file=str(path),
                url=utils.safe_url_for_log(spec.url),
            )
            attempts.append(attempt)
            return {"provider": provider, "attempts": attempts, **attempt}
        except Exception as error:
            attempt["error"] = (
                f"{type(error).__name__}: {utils.safe_text_for_log(error)}"
            )
            attempts.append(attempt)
    return {"status": "failed", "attempts": attempts}


def _write_report(results: list[dict], json_path: Path, markdown_path: Path) -> None:
    json_path.write_text(
        json.dumps(results, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    lines = [
        "# CLI-recommended APK download verification",
        "",
        "| App | Patch source | CLI recommendation | Mode | Provider | Result | Bytes |",
        "|---|---|---|---|---|---:|---:|",
    ]
    for item in results:
        lines.append(
            "| {app} | {source} | `{version}` | {mode} | {provider} | "
            "{status} | {bytes} |".format(
                app=item["app"],
                source=item["source"],
                version=item.get("version", "unresolved"),
                mode=item.get("mode", "—"),
                provider=item.get("provider", "—"),
                status=item.get("status", "failed").upper(),
                bytes=item.get("bytes", "—"),
            )
        )
    markdown_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--matrix", type=Path, default=Path("temp/cli-recommended-versions.json")
    )
    parser.add_argument(
        "--output-dir", type=Path, default=Path("temp/cli-recommended-apks")
    )
    parser.add_argument(
        "--json-output",
        type=Path,
        default=Path("temp/cli-recommended-downloads.json"),
    )
    parser.add_argument(
        "--markdown-output",
        type=Path,
        default=Path("temp/cli-recommended-downloads.md"),
    )
    parser.add_argument("--start", type=int, default=0)
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()

    rows = json.loads(args.matrix.read_text(encoding="utf-8"))
    selected = rows[args.start :]
    if args.limit is not None:
        selected = selected[: args.limit]
    args.output_dir.mkdir(parents=True, exist_ok=True)

    existing: dict[tuple[str, str, str | None], dict] = {}
    results: list[dict] = []
    for row in selected:
        result = {"app": row["app"], "source": row["source"], "status": "failed"}
        try:
            candidate, mode = _candidate_from_row(row)
            result.update(version=candidate.describe(), mode=mode)
            key = (row["app"], candidate.name, candidate.code)
            if key in existing:
                download = {**existing[key], "reused_across_sources": True}
            else:
                download = _download(row, candidate, args.output_dir)
                if download.get("status") == "ok":
                    existing[key] = download
            result.update(download)
        except Exception as error:
            result["error"] = (
                f"{type(error).__name__}: {utils.safe_text_for_log(error)}"
            )
        results.append(result)
        print(
            f"{result['status'].upper()}: {row['app']} / {row['source']} "
            f"{result.get('version', '')} via {result.get('provider', 'none')}",
            flush=True,
        )
        _write_report(results, args.json_output, args.markdown_output)

    return 0 if all(item["status"] == "ok" for item in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
