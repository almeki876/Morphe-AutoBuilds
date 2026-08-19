import re
import logging
import os
import random
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import List, Optional
from src import gh
from sys import exit
import subprocess
from pathlib import Path
from urllib.parse import urlparse, unquote, parse_qs, urlsplit
from src.versioning import VersionCandidate, parse_candidates


RETRYABLE_HTTP_STATUSES = frozenset({403, 408, 425, 429, 500, 502, 503, 504})
_BLOCKED_HOSTS: set[str] = set()


class BotProtectionError(RuntimeError):
    """A host returned an interactive anti-bot page instead of the resource."""


def is_bot_challenge(response) -> bool:
    """Recognize Cloudflare challenge pages without relying on one status code."""
    if response is None:
        return False
    if str(response.headers.get("cf-mitigated", "")).casefold() == "challenge":
        return True
    content_type = str(response.headers.get("content-type", "")).casefold()
    if "html" not in content_type:
        return False
    body = response.content[:65536].lower()
    markers = (
        b"/cdn-cgi/challenge-platform/",
        b"cf_chl_",
        b"challenges.cloudflare.com",
        b"enable javascript and cookies to continue",
        b"just a moment",
    )
    return any(marker in body for marker in markers)


def clear_blocked_hosts() -> None:
    """Reset the per-process challenge circuit breaker (primarily for probes)."""
    _BLOCKED_HOSTS.clear()


def _positive_number_from_env(name: str, default: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
        return value if value > 0 else default
    except (TypeError, ValueError):
        return default


def safe_url_for_log(url: str) -> str:
    """Drop query strings/fragments so signed download credentials are never logged."""
    parts = urlsplit(url)
    hostname = (parts.hostname or "").casefold()
    path = parts.path
    if hostname == "dw.uptodown.com" and path.startswith("/dwn/"):
        path = "/dwn/<redacted>"
    elif hostname == "url-provider.aptoide.com" and path.startswith("/download/"):
        path = "/download/<redacted>"
    return f"{parts.scheme}://{parts.netloc}{path}"


def safe_text_for_log(value: object, limit: int = 500) -> str:
    """Redact every HTTP URL embedded in an exception or diagnostic message."""
    text = str(value).replace("\r", " ").replace("\n", " ").strip()
    text = re.sub(
        r"https?://[^\s)\]}>,;]+",
        lambda match: safe_url_for_log(match.group(0)),
        text,
    )
    return text[:limit]


def retry_after_seconds(response, attempt: int) -> float:
    """Honor Retry-After, otherwise return capped exponential backoff with jitter."""
    cap = _positive_number_from_env("HTTP_RETRY_MAX_SECONDS", 60)
    base = _positive_number_from_env("HTTP_RETRY_BASE_SECONDS", 5)
    header = response.headers.get("Retry-After") if response is not None else None

    if header:
        try:
            return min(cap, max(0.0, float(header)))
        except (TypeError, ValueError):
            try:
                retry_at = parsedate_to_datetime(header)
                if retry_at.tzinfo is None:
                    retry_at = retry_at.replace(tzinfo=timezone.utc)
                seconds = (retry_at - datetime.now(timezone.utc)).total_seconds()
                return min(cap, max(0.0, seconds))
            except (TypeError, ValueError, OverflowError, IndexError):
                pass

    exponential = base * (2 ** max(0, attempt - 1))
    jitter = random.uniform(0, min(base, 5))
    return min(cap, exponential + jitter)


def cf_aware_get(url: str, retries: int | None = None, **kwargs):
    """GET with retries and a fail-fast circuit breaker for bot challenges.

    Interactive Cloudflare challenges cannot be reliably completed by a
    headless GitHub Actions job. Retrying the same challenge for every version
    only wastes minutes and increases blocking. Once a host serves a confirmed
    challenge, further requests to that host fail immediately and the
    orchestrator can continue with another provider. Ordinary throttling and
    transport failures still use bounded exponential backoff.
    """
    from src import reset_http_session, session as current_session

    _session = current_session

    if retries is None:
        retries = int(_positive_number_from_env("HTTP_RETRIES", 4))
    retries = max(1, retries)
    kwargs.setdefault("timeout", 30)
    safe_url = safe_url_for_log(url)
    hostname = (urlparse(url).hostname or "").casefold()
    if hostname in _BLOCKED_HOSTS:
        raise BotProtectionError(
            f"interactive bot protection already detected for {hostname}"
        )

    for attempt in range(1, retries + 1):
        response = None
        try:
            response = _session.get(url, **kwargs)
            if is_bot_challenge(response):
                challenge_hostname = (
                    urlparse(response.url).hostname or hostname
                ).casefold()
                if challenge_hostname:
                    _BLOCKED_HOSTS.add(challenge_hostname)
                status = response.status_code
                challenge_url = safe_url_for_log(response.url)
                response.close()
                raise BotProtectionError(
                    f"interactive bot protection returned HTTP {status} "
                    f"for {challenge_url}"
                )
            if response.status_code == 403 and hostname in {
                "www.apkmirror.com",
                "apkcombo.com",
                "apkpure.com",
            }:
                _BLOCKED_HOSTS.add(hostname)
                response.close()
                raise BotProtectionError(
                    f"HTTP 403 blocked automated requests to {hostname}"
                )

            if (
                response.status_code not in RETRYABLE_HTTP_STATUSES
                or attempt >= retries
            ):
                return response

            wait = retry_after_seconds(response, attempt)
            logging.warning(
                "HTTP %s from %s (attempt %d/%d); retrying in %.1fs",
                response.status_code,
                safe_url,
                attempt,
                retries,
                wait,
            )
            response.close()
        except BotProtectionError:
            raise
        except Exception as error:
            if attempt >= retries:
                raise
            _session = reset_http_session()
            wait = retry_after_seconds(None, attempt)
            logging.warning(
                "Request to %s failed on attempt %d/%d (%s); "
                "resetting session and retrying in %.1fs",
                safe_url,
                attempt,
                retries,
                error,
                wait,
            )

        time.sleep(wait)

    raise RuntimeError(f"GET retry loop ended unexpectedly for {safe_url}")


def _parseparam(s):
    while s[:1] == ";":
        s = s[1:]
        end = s.find(";")
        while end > 0 and (s.count('"', 0, end) - s.count('\\"', 0, end)) % 2:
            end = s.find(";", end + 1)
        if end < 0:
            end = len(s)
        f = s[:end]
        yield f.strip()
        s = s[end:]


def parse_header(line):
    """Parse a Content-type like header.
    Return the main content-type and a dictionary of options.
    """
    parts = _parseparam(";" + line)
    key = parts.__next__()
    pdict = {}
    for p in parts:
        i = p.find("=")
        if i >= 0:
            name = p[:i].strip().lower()
            value = p[i + 1 :].strip()
            if len(value) >= 2 and value[0] == value[-1] == '"':
                value = value[1:-1]
                value = value.replace("\\\\", "\\").replace('\\"', '"')
            pdict[name] = value
    return key, pdict

def find_file(files: list[Path], prefix: str = None, suffix: str = None, contains: str = None, exclude: list = None) -> Path | None:
    """Find a file with various matching criteria"""
    if exclude is None:
        exclude = []
    
    for file in files:
        # Skip excluded patterns
        if any(excl.lower() in file.name.lower() for excl in exclude):
            continue
            
        # Check all criteria
        matches = True
        
        if prefix and not file.name.startswith(prefix):
            matches = False
            
        if suffix and not file.name.endswith(suffix):
            matches = False
            
        if contains and contains.lower() not in file.name.lower():
            matches = False
            
        if matches:
            return file
    
    # If not found with exclude, try without exclude (for fallback)
    if exclude:
        for file in files:
            matches = True
            
            if prefix and not file.name.startswith(prefix):
                matches = False
                
            if suffix and not file.name.endswith(suffix):
                matches = False
                
            if contains and contains.lower() not in file.name.lower():
                matches = False
                
            if matches:
                return file
    
    return None


def find_latest_patch_bundle(
    files: list[Path], suffixes: tuple[str, ...]
) -> Path | None:
    """Select the newest versioned patch bundle from a release's assets."""
    candidates = [
        file for file in files
        if file.suffix in suffixes and "patches" in file.name.lower()
    ]
    if not candidates:
        candidates = [file for file in files if file.suffix in suffixes]
    if not candidates:
        return None

    def version_key(file: Path) -> tuple[list[int], str]:
        version_match = re.search(
            r"(?:^|[-_])v?(\d+(?:\.\d+)+)", file.stem, re.IGNORECASE
        )
        version = version_match.group(1) if version_match else "0"
        return normalize_version(version), file.name

    return max(candidates, key=version_key)

def find_apksigner() -> str | None:
    sdk_root = Path("/usr/local/lib/android/sdk")
    build_tools_dir = sdk_root / "build-tools"

    if not build_tools_dir.exists():
        logging.error(f"No build-tools found at: {build_tools_dir}")
        return None

    versions = sorted(build_tools_dir.iterdir(), reverse=True)
    for version_dir in versions:
        apksigner_path = version_dir / "apksigner"
        if apksigner_path.exists() and apksigner_path.is_file():
            return str(apksigner_path)

    logging.error("No apksigner found in build-tools")
    return None

def run_process(
    command: List[str],
    cwd: Optional[Path] = None,
    capture: bool = False,
    stream: bool = False,
    silent: bool = False,
    check: bool = True,
    shell: bool = False
) -> Optional[str]:
    process = subprocess.Popen(
        command,
        cwd=str(cwd) if cwd else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        shell=shell
    )

    output_lines = []

    try:
        for line in iter(process.stdout.readline, ''):
            if line:
                if not silent:
                    print(line.rstrip(), flush=True)
                if capture:
                    output_lines.append(line)
        process.stdout.close()
        return_code = process.wait()

        if check and return_code != 0:
            raise subprocess.CalledProcessError(return_code, command)

        return ''.join(output_lines).strip() if capture else None

    except FileNotFoundError:
        print(f"Command not found: {command[0]}", flush=True)
        exit(1)
    except Exception as e:
        print(f"Error while running command: {e}", flush=True)
        exit(1)

def normalize_version(version: str) -> list[int]:
    parts = version.split('.')
    normalized = []
    for part in parts:
        match = re.match(r'(\d+)', part)
        if match:
            normalized.append(int(match.group(1)))
        else:
            normalized.append(0)
    
    # Include build number in comparison for versions like "6.6 build 002"
    build_match = re.search(r'build\s+(\d+)', version, re.IGNORECASE)
    if build_match:
        normalized.append(int(build_match.group(1)))
    
    # Also check for parentheses format like "32.30.0(1575420)"
    paren_match = re.search(r'\((\d+)\)$', version)
    if paren_match:
        normalized.append(int(paren_match.group(1)))
    
    return normalized

def get_highest_version(versions: list[str]) -> str | None:
    if not versions:
        return None
    highest_version = versions[0]
    for v in versions[1:]:
        if normalize_version(v) > normalize_version(highest_version):
            highest_version = v
    return highest_version

def get_supported_version_candidates(
    package_name: str, cli: str, patches: str
) -> list[VersionCandidate]:
    # Morphe CLI and ReVanced CLI have different list-versions syntax.
    # CLI-kind detection lives in src/cli_compat.py (single source of truth);
    # imported locally to avoid a circular import (cli_compat itself uses
    # this module's run_process()).
    from src import cli_compat
    kind = cli_compat.detect_cli_kind(Path(cli))
    is_morphe_cli = kind == cli_compat.MORPHE
    is_revanced_v5_or_newer = kind == cli_compat.REVANCED_V5PLUS

    if is_morphe_cli:
        # morphe-cli は --patches フラグを使う（-p は存在しない）
        morphe_patches_flag = '--patches'
        cmd = [
            'java', '-jar', cli,
            'list-versions',
            morphe_patches_flag, patches,
            '-f', package_name,
        ]
    elif is_revanced_v5_or_newer:
        # ReVanced CLI v5+: list-versions <bundle> [-f <package>]
        cmd = [
            'java', '-jar', cli,
            'list-versions',
            patches,
            '-f', package_name,
        ]
    else:
        # ReVanced CLI v4: list-versions [-f <package>] <bundle>
        cmd = [
            'java', '-jar', cli,
            'list-versions',
            '-f', package_name,
            patches,
        ]

    output = run_process(cmd, capture=True, silent=True, check=False)

    if not output:
        logging.warning("No output returned from list-versions command")
        return []

    lines = output.splitlines()
    logging.info(f"CLI raw output lines: {lines}")

    # Detect CLI error/usage output (wrong syntax, unrecognized args, etc.)
    # Check all lines because Morphe CLI prefixes output with "INFO: Running in Headless environment..."
    all_output_lower = output.lower()
    if 'missing required option' in all_output_lower or 'unmatched argument' in all_output_lower:
        logging.warning(f"CLI returned error/usage output (missing option or unmatched arg), cannot determine version")
        return []
    first_line = lines[0].strip().lower()
    if 'usage:' in first_line or 'error' in first_line:
        logging.warning(f"CLI returned error/usage output, cannot determine version")
        return []

    candidates = parse_candidates(output)
    if not candidates:
        logging.warning("No supported versions found")
        return []

    candidates.sort(key=lambda item: normalize_version(item.name), reverse=True)
    logging.info(
        "CLI parsed compatible versions: %s",
        [candidate.describe() for candidate in candidates],
    )
    return candidates


def get_supported_versions(package_name: str, cli: str, patches: str) -> list[str]:
    """Backward-compatible list of version names for older callers."""
    return [
        candidate.name
        for candidate in get_supported_version_candidates(package_name, cli, patches)
    ]


def get_supported_version(package_name: str, cli: str, patches: str) -> Optional[str]:
    """Backward-compatible helper returning only the newest compatible version."""
    versions = get_supported_versions(package_name, cli, patches)
    return versions[0] if versions else None

def extract_filename(response, fallback_url=None) -> str:
    cd = response.headers.get('content-disposition')
    if cd:
        _, params = parse_header(cd)
        filename = params.get('filename') or params.get('filename*')
        if filename:
            return unquote(filename)

    parsed = urlparse(response.url)
    query_params = parse_qs(parsed.query)
    rcd = query_params.get('response-content-disposition')
    if rcd:
        _, params = parse_header(unquote(rcd[0]))
        filename = params.get('filename') or params.get('filename*')
        if filename:
            return unquote(filename)

    path = urlparse(fallback_url or response.url).path
    return unquote(Path(path).name)

def detect_github_release(
    user: str,
    repo: str,
    tag: str,
    retries: int = 3,
    retry_delay: int = 10,
    *,
    include_prereleases: bool = True,
) -> dict:
    import time

    last_err = None
    for attempt in range(1, retries + 1):
        try:
            return _detect_github_release_once(
                user,
                repo,
                tag,
                include_prereleases=include_prereleases,
            )
        except Exception as e:
            last_err = e
            if attempt < retries:
                logging.warning(
                    f"⚠️  GitHub release fetch failed for {user}/{repo} "
                    f"(attempt {attempt}/{retries}): {e} — retrying in {retry_delay}s..."
                )
                time.sleep(retry_delay)
            else:
                logging.error(
                    f"❌ GitHub release fetch failed for {user}/{repo} "
                    f"after {retries} attempts: {e}"
                )
    raise last_err


def _detect_github_release_once(
    user: str,
    repo: str,
    tag: str,
    *,
    include_prereleases: bool = True,
) -> dict:
    repo_obj = gh.get_repo(f"{user}/{repo}")

    if tag in ["latest", "latest-tag"]:
        releases = list(repo_obj.get_releases())
        if not include_prereleases:
            releases = [
                release
                for release in releases
                if not release.prerelease and not release.draft
            ]
        if not releases:
            raise ValueError(f"No releases found for {user}/{repo}")
        release = max(releases, key=lambda x: x.created_at)
        logging.info(f"Fetched latest release: {release.tag_name}")
        return release.raw_data

    if tag in ["", "dev", "prerelease"]:
        releases = list(repo_obj.get_releases())
        if not releases:
            raise ValueError(f"No releases found for {user}/{repo}")

        if tag == "":
            release = max(releases, key=lambda x: x.created_at)
        elif tag == "dev":
            devs = [r for r in releases if 'dev' in r.tag_name.lower()]
            if not devs:
                raise ValueError(f"No dev release found for {user}/{repo}")
            release = max(devs, key=lambda x: x.created_at)
        else:
            pres = [r for r in releases if r.prerelease]
            if not pres:
                raise ValueError(f"No prerelease found for {user}/{repo}")
            release = max(pres, key=lambda x: x.created_at)

        logging.info(f"Fetched release: {release.tag_name}")
        return release.raw_data

    try:
        release = repo_obj.get_release(tag)
        logging.info(f"Fetched release: {release.tag_name}")
        return release.raw_data
    except Exception as e:
        logging.error(f"Error fetching release {tag} for {user}/{repo}: {e}")
        raise


def detect_gitlab_release(
    user: str,
    repo: str,
    tag: str = "latest",
    retries: int = 3,
    retry_delay: int = 10,
) -> dict:
    import json
    import urllib.parse
    import urllib.request

    project_slug = urllib.parse.quote(f"{user}/{repo}", safe="")
    url = f"https://gitlab.com/api/v4/projects/{project_slug}/releases"

    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Morphe-AutoBuilds", "Accept": "application/json"},
    )
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            with urllib.request.urlopen(req) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                if not data:
                    raise ValueError(f"No releases found for GitLab repo {user}/{repo}")

                selected = None
                if tag in ["latest", "latest-tag", ""]:
                    selected = data[0]
                else:
                    for rel in data:
                        if rel.get("tag_name") == tag or rel.get("name") == tag:
                            selected = rel
                            break
                    if not selected:
                        selected = data[0]

                assets = []
                for link in selected.get("assets", {}).get("links", []):
                    download_url = link.get("direct_asset_url") or link.get("url")
                    assets.append(
                        {
                            "name": link.get("name"),
                            "browser_download_url": download_url,
                        }
                    )
                for src in selected.get("assets", {}).get("sources", []):
                    assets.append(
                        {
                            "name": f"{repo}-{selected.get('tag_name')}.{src.get('format')}",
                            "browser_download_url": src.get("url"),
                        }
                    )

                logging.info(f"Fetched GitLab release: {selected.get('tag_name')}")
                return {
                    "tag_name": selected.get("tag_name"),
                    "name": selected.get("name"),
                    "assets": assets,
                    "created_at": selected.get("created_at"),
                }
        except Exception as e:
            last_err = e
            if attempt < retries:
                logging.warning(
                    f"⚠️  GitLab release fetch failed for {user}/{repo} "
                    f"(attempt {attempt}/{retries}): {e} — retrying in {retry_delay}s..."
                )
                time.sleep(retry_delay)
            else:
                logging.error(
                    f"❌ GitLab release fetch failed for {user}/{repo} "
                    f"after {retries} attempts: {e}"
                )
    raise last_err
