import os
import logging
import builtins
from curl_cffi import requests
from curl_cffi.requests.impersonate import DEFAULT_CHROME
from github import Auth, Github


def create_http_session():
    """Create a fresh browser-impersonating session for public APK sites."""
    return requests.Session(impersonate=DEFAULT_CHROME)


session = create_http_session()


def reset_http_session():
    """Replace a session whose connection pool or DNS state became unhealthy."""
    global session
    try:
        session.close()
    except Exception:
        pass
    session = create_http_session()
    return session

# Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

# Env Vars
github_token = os.getenv('GITHUB_TOKEN')
repository = os.getenv('GITHUB_REPOSITORY')
endpoint_url = os.getenv('ENDPOINT_URL')
access_key_id = os.getenv('AWS_ACCESS_KEY_ID')
secret_access_key = os.getenv('AWS_SECRET_ACCESS_KEY')
bucket_name = os.getenv('BUCKET_NAME')

# APKmirror base url
base_url = "https://www.apkmirror.com"
gh = Github(auth=Auth.Token(github_token)) if github_token else Github()

# aurora_play historically referenced this locale as a module-global default.
# Keep the compatibility name available during package initialization while the
# downloader itself remains responsible for choosing its explicit locale.
# The primary gplaydl request must include the base English locale as well as
# Japanese so the resulting payload remains compatible with existing consumers.
if not hasattr(builtins, "DEFAULT_GPLAYDL_LOCALES"):
    builtins.DEFAULT_GPLAYDL_LOCALES = "en-US,ja"

# The APK cache namespace is part of the build input contract. Enforce it
# before any module imports src.apk_cache and snapshots CACHE_TAG.
from src import cache_contract as _cache_contract
_cache_contract.enforce()

# Gboard is the only normal-build exception that combines multiple Morphe
# bundles on one APK.  Install the command adapter after package globals are
# initialized so src.utils can import this module without a circular init race.
from src import gboard_multi as _gboard_multi
_gboard_multi.install()

# Derive patch/version behavior from the currently downloaded upstream bundle.
# This mutates only the ephemeral CI working copy: committed local options and
# provider fallback metadata remain intact, but they cannot override upstream
# recommendation/version policy for the current build.
from src import upstream_policy as _upstream_policy
_upstream_policy.prepare_runtime_policy()
