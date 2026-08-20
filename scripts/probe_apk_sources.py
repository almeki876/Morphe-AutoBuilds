"""Live diagnostics for APK provider configuration and download contracts.

Examples:
  python scripts/probe_apk_sources.py --app nova --version 8.8.6 --code 88600
  python scripts/probe_apk_sources.py --app icon-packer \
      --version 1.21.0-release --providers apkpure --download-dir temp/probe

Signed query strings are never printed. Without ``--download-dir`` the script
requests only the first bytes needed to confirm that a resolved URL is an APK.
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import apk_cache, providers, utils
from src.downloads import DownloadSpec, normalize_download
from src.downloader import download_resource
from src.versioning import VersionCandidate


def _probe_archive(url: str, headers: dict[str, str]) -> tuple[int, str]:
    headers = {**headers, "Range": "bytes=0-3"}
    response = utils.cf_aware_get(
        url,
        headers=headers,
        stream=True,
        timeout=45,
        retries=2,
    )
    try:
        response.raise_for_status()
        first = b""
        for chunk in response.iter_content(chunk_size=4):
            first += chunk
            if len(first) >= 4:
                break
        if first[:2] != b"PK":
            content_type = response.headers.get("content-type", "")
            raise ValueError(
                f"response is not an APK archive (content-type={content_type!r})"
            )
        total = response.headers.get("content-range")
        return response.status_code, str(total or response.headers.get("content-length", ""))
    finally:
        response.close()


def _resolve(
    provider: str,
    app_name: str,
    config: dict,
    candidate: VersionCandidate,
) -> str | DownloadSpec:
    module = providers.MODULES[provider]
    candidate_resolver = getattr(module, "get_download_link_for_candidate", None)
    if candidate_resolver:
        link = candidate_resolver(candidate, app_name, config)
        if link:
            return link
        raise ValueError("provider returned no link for candidate")

    errors: list[str] = []
    for alias in candidate.aliases(provider):
        try:
            link = module.get_download_link(alias, app_name, config)
            if link:
                return link
        except Exception as error:
            errors.append(f"{alias}: {type(error).__name__}: {error}")
    raise ValueError("; ".join(errors) or "provider returned no download link")


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--app", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--code")
    parser.add_argument("--arch", default="arm64-v8a")
    parser.add_argument(
        "--providers",
        help="Comma-separated provider names; defaults to configured priority",
    )
    parser.add_argument("--download-dir", type=Path)
    parser.add_argument(
        "--require-all",
        action="store_true",
        help="Exit non-zero unless every selected provider succeeds",
    )
    parser.add_argument(
        "--stop-after-success",
        action="store_true",
        help="Stop after the first provider that passes all checks",
    )
    return parser.parse_args()


def main() -> int:
    args = _arguments()
    logging.getLogger().setLevel(logging.WARNING)
    candidate = VersionCandidate(name=args.version, code=args.code)
    selected = (
        tuple(item.strip() for item in args.providers.split(",") if item.strip())
        if args.providers
        else providers.download_priority(args.app)
    )

    results: list[dict] = []
    for provider in selected:
        result = {"provider": provider, "status": "failed"}
        try:
            if provider not in providers.MODULES:
                raise ValueError(f"unknown provider: {provider}")
            config = providers.load_config(args.app, provider)
            if config is None:
                result.update(status="skipped", error="no provider config")
                results.append(result)
                continue
            config["arch"] = args.arch
            download_spec = normalize_download(
                _resolve(provider, args.app, config, candidate)
            )
            link = download_spec.url
            result["url"] = utils.safe_url_for_log(link)
            referer = download_spec.headers.get("Referer") or providers.referer(
                provider,
                args.app,
                config,
            )
            request_headers = dict(download_spec.headers)
            if referer and "Referer" not in request_headers:
                request_headers["Referer"] = referer

            if args.download_dir:
                args.download_dir.mkdir(parents=True, exist_ok=True)
                safe_app = re.sub(r"[^A-Za-z0-9._-]+", "-", args.app).strip("-")
                safe_version = re.sub(
                    r"[^A-Za-z0-9._-]+",
                    "-",
                    candidate.canonical,
                ).strip("-")
                target = args.download_dir / (
                    f"{safe_app}-{provider}-{safe_version}.apk"
                )
                path = download_resource(
                    link,
                    name=str(target),
                    referer=None if "Referer" in download_spec.headers else referer,
                    headers=download_spec.headers,
                    validate_apk=True,
                )
                if not apk_cache.is_valid_apk_archive(path):
                    raise ValueError("downloaded file is not a valid APK archive")
                result.update(
                    status="ok",
                    bytes=path.stat().st_size,
                    file=str(path),
                )
            else:
                status, size = _probe_archive(
                    link,
                    request_headers,
                )
                result.update(status="ok", http_status=status, size=size)
        except Exception as error:
            result["error"] = (
                f"{type(error).__name__}: {utils.safe_text_for_log(error)}"
            )
        results.append(result)
        if args.stop_after_success and result["status"] == "ok":
            break

    print(json.dumps(results, ensure_ascii=False, indent=2))
    successes = sum(result["status"] == "ok" for result in results)
    failures = sum(result["status"] == "failed" for result in results)
    if successes == 0 or (args.require_all and failures):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
