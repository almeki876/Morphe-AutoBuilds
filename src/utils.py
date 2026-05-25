import re
import logging
from typing import List, Optional
from src import gh
from sys import exit
import subprocess
from pathlib import Path
from urllib.parse import urlparse, unquote, parse_qs

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

def get_supported_version(package_name: str, cli: str, patches: str) -> Optional[str]:
    # Morphe CLI and ReVanced CLI have different list-versions syntax
    cli_name = Path(cli).name.lower()
    is_morphe_cli = 'morphe' in cli_name
    is_revanced_v6_or_newer = 'revanced-cli-6' in cli_name or 'revanced-cli-7' in cli_name or 'revanced-cli-8' in cli_name

    if is_morphe_cli:
        cmd = [
            'java', '-jar', cli,
            'list-versions',
            '--patches', patches,
            '-f', package_name,
        ]
    elif is_revanced_v6_or_newer:
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
        return None

    lines = output.splitlines()
    logging.info(f"CLI raw output lines: {lines}")

    # Detect CLI error/usage output (wrong syntax, unrecognized args, etc.)
    # Check all lines because Morphe CLI prefixes output with "INFO: Running in Headless environment..."
    all_output_lower = output.lower()
    if 'missing required option' in all_output_lower or 'unmatched argument' in all_output_lower:
        logging.warning(f"CLI returned error/usage output (missing option or unmatched arg), cannot determine version")
        return None
    first_line = lines[0].strip().lower()
    if 'usage:' in first_line or 'error' in first_line:
        logging.warning(f"CLI returned error/usage output, cannot determine version")
        return None

    if len(lines) <= 2:
        logging.warning("Output has no version lines")
        return None

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
        return None

    logging.info(f"CLI parsed versions: {versions}")
    return get_highest_version(versions)

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

def detect_github_release(user: str, repo: str, tag: str, retries: int = 3, retry_delay: int = 10) -> dict:
    import time

    last_err = None
    for attempt in range(1, retries + 1):
        try:
            return _detect_github_release_once(user, repo, tag)
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


def _detect_github_release_once(user: str, repo: str, tag: str) -> dict:
    repo_obj = gh.get_repo(f"{user}/{repo}")

    if tag in ["latest", "latest-tag"]:
        # get_latest_release() returns the latest stable (non-prerelease) release
        release = repo_obj.get_latest_release()
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

def detect_source_type(cli_file: Path, patches_file: Path) -> str:
    """Detect if we're using Morphe or ReVanced based on downloaded files"""
    if cli_file and "morphe" in cli_file.name.lower() and patches_file and patches_file.suffix == ".mpp":
        return "morphe"
    elif cli_file and "revanced" in cli_file.name.lower() and patches_file and patches_file.suffix in [".jar", ".rvp"]:
        return "revanced"
    return "unknown"
