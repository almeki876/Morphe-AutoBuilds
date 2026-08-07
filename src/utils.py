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


RETRYABLE_HTTP_STATUSES = frozenset({403, 408, 425, 429, 500, 502, 503, 504})


def _positive_number_from_env(name: str, default: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
        return value if value > 0 else default
    except (TypeError, ValueError):
        return default


def safe_url_for_log(url: str) -> str:
    """Drop query strings/fragments so signed download credentials are never logged."""
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}{parts.path}"


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
    """GET with bounded retries and a Cloudflare Turnstile bypass.

    Used by apkmirror/apkpure/uptodown, which are all fronted by Cloudflare
    and intermittently return challenges or throttling responses to Actions
    runners. 403/429/5xx responses and transport errors use capped exponential
    backoff with jitter, while Retry-After is honored when present.
    """
    from src import session as _session

    if retries is None:
        retries = int(_positive_number_from_env("HTTP_RETRIES", 4))
    retries = max(1, retries)
    kwargs.setdefault("timeout", 30)
    safe_url = safe_url_for_log(url)

    for attempt in range(1, retries + 1):
        response = None
        try:
            response = _session.get(url, **kwargs)
            try:
                from src.cf_bypass import is_cf_challenge, solve_cloudflare

                if is_cf_challenge(response):
                    logging.info(
                        "Cloudflare challenge on %s — launching bypass…", safe_url
                    )
                    cookies = solve_cloudflare(url)
                    if cookies:
                        hostname = urlparse(url).hostname or ""
                        domain = "." + ".".join(hostname.split(".")[-2:])
                        for name, value in cookies.items():
                            _session.cookies.set(name, value, domain=domain)
                        response.close()
                        response = _session.get(url, **kwargs)
                        if response.status_code == 403:
                            logging.warning(
                                "Cloudflare bypass got cookies but retry still "
                                "returned 403 for %s", safe_url
                            )
                        else:
                            logging.info(
                                "Cloudflare bypass succeeded for %s (status %s)",
                                safe_url,
                                response.status_code,
                            )
                    else:
                        logging.warning(
                            "Cloudflare bypass could not obtain cookies for %s",
                            safe_url,
                        )
            except ImportError:
                logging.debug("nodriver not installed; skipping Cloudflare bypass")
            except Exception as bypass_error:
                logging.debug(
                    "Cloudflare bypass attempt failed for %s: %s",
                    safe_url,
                    bypass_error,
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
        except Exception as error:
            if attempt >= retries:
                raise
            wait = retry_after_seconds(None, attempt)
            logging.warning(
                "Request to %s failed on attempt %d/%d (%s); retrying in %.1fs",
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

def get_supported_versions(package_name: str, cli: str, patches: str) -> list[str]:
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

    versions = []
    for line in lines:
        line = line.strip()
        # Strip Morphe CLI INFO: prefix if present
        if line.lower().startswith('info:') or line.lower().startswith('warning:'):
            continue
        if line and 'Any' not in line:
            # Parse version - CLIの出力形式は複数パターンある:
            #   "6.6 build 002"           -> バージョン名
            #   "32.30.0(1575420)"        -> バージョン名
            #   "81042 (8.5.1) (1 patch)" -> vercode (vername) ... Morphe形式
            #   "4.12.81 (1 patch)"       -> バージョン名 (パッチ数)
            parts = line.split()
            if parts:
                first = parts[0]
                # Validate it looks like a version (starts with a digit)
                if not first[0].isdigit():
                    continue

                # Morphe形式: "vercode (vername) ..." -> vername を優先
                # parts[1] が "(x.y.z)" の括弧付きバージョン名かチェック
                if len(parts) >= 2 and parts[1].startswith("(") and parts[1].endswith(")"):
                    inner = parts[1][1:-1]  # 括弧を除去
                    if re.match(r"^\d[\d.]+$", inner):
                        versions.append(inner)
                        continue

                version = first
                # Check if next parts are "build XXX"
                if len(parts) >= 3 and parts[1].lower() == "build":
                    version = f"{parts[0]} build {parts[2]}"
                versions.append(version)

    if not versions:
        logging.warning("No supported versions found")
        return []

    # De-duplicate while preserving every supported alternative. Providers
    # frequently remove the newest build before all mirrors have it, so callers
    # must be able to try older compatible versions automatically.
    unique_versions = list(dict.fromkeys(versions))
    unique_versions.sort(key=normalize_version, reverse=True)
    logging.info(f"CLI parsed compatible versions: {unique_versions}")
    return unique_versions


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
