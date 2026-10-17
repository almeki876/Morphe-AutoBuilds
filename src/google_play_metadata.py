"""Resolve current-release identities from Google Play metadata.

Google Play exposes versionName and versionCode together through its details
response.  This module is the single place that queries that identity so both
patch-version resolution and update monitoring use the same authenticated,
device-profile-aware implementation.

Robustness design
-----------------
* Infrastructure failures (local dispenser cannot start, authentication fails,
  lookup hangs) are told apart from per-package outcomes (package not found,
  incomplete details).  Only the former count towards disabling Google Play.
* After ``GPLAYDL_MAX_INFRA_FAILURES`` (default 2) consecutive infrastructure
  failures Google Play is considered unavailable for the rest of the process.
  Before this, every call re-launched the local dispenser (~1 minute each) so a
  dead dispenser consumed the whole job timeout.
* The dispenser start-up and each lookup are bounded by timeouts
  (``GPLAYDL_DISPENSER_START_TIMEOUT`` default 900s, ``GPLAYDL_LOOKUP_TIMEOUT``
  default 180s) so a hang cannot block the caller indefinitely.
* ``current_release_identity(..., strict=True)`` raises typed exceptions so
  callers can distinguish "give up on Google Play" from "this package failed".
  The default (``strict=False``) keeps the historical contract: never raise,
  return ``None``.
* Failures are logged at WARNING with exception *type names only* (no messages,
  which may carry credentials).  They used to be logged at INFO and were
  invisible in CI.
"""

from __future__ import annotations

import logging
import os
import threading
from collections.abc import Callable, Iterable
from typing import Any, TypeVar

from src import local_gplaydl_dispenser
from src.gplaydl_profile_retry import ordered_priority_profiles
from src.versioning import VersionCandidate, remember_version_code

T = TypeVar("T")


# --------------------------------------------------------------------------- #
# Exceptions
# --------------------------------------------------------------------------- #
class GooglePlayError(Exception):
    """Base class for Google Play metadata failures (messages are log-safe)."""


class PlayLookupError(GooglePlayError):
    """A single lookup failed; trying another package may still work."""


class PlayUnavailableError(GooglePlayError):
    """Google Play is unusable for this process; callers should stop trying."""


class PlayNotConfiguredError(PlayUnavailableError):
    """No Google Play credentials are configured at all."""


# --------------------------------------------------------------------------- #
# Configuration helpers
# --------------------------------------------------------------------------- #
def _env_int(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, "").strip() or default)
    except ValueError:
        return default
    return value if value >= 0 else default


def _max_infra_failures() -> int:
    return max(1, _env_int("GPLAYDL_MAX_INFRA_FAILURES", 2))


def _dispenser_start_timeout() -> int:
    # Safety net only.  A cold start (git clone + go build + docker pull) took
    # about 6 minutes on CI, so this must stay well above that; the dispenser
    # module enforces tighter per-phase timeouts itself.
    return _env_int("GPLAYDL_DISPENSER_START_TIMEOUT", 900)


def _lookup_timeout() -> int:
    return _env_int("GPLAYDL_LOOKUP_TIMEOUT", 180)


# --------------------------------------------------------------------------- #
# Process-wide health state
# --------------------------------------------------------------------------- #
class _Health:
    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.failures = 0
        self.unavailable_reason: str | None = None
        self.last_error: str | None = None


_health = _Health()
_health_lock = threading.Lock()


def play_health() -> dict[str, Any]:
    """Snapshot of Google Play health, for reports and diagnostics."""
    with _health_lock:
        return {
            "available": _health.unavailable_reason is None,
            "consecutive_failures": _health.failures,
            "unavailable_reason": _health.unavailable_reason,
            "last_error": _health.last_error,
        }


def reset_health() -> None:
    """Forget recorded failures (mainly for tests)."""
    with _health_lock:
        _health.reset()


def _record_success() -> None:
    with _health_lock:
        _health.failures = 0


def _mark_unavailable(reason: str) -> PlayUnavailableError:
    with _health_lock:
        _health.last_error = reason
        if _health.unavailable_reason is None:
            _health.unavailable_reason = reason
    logging.warning("Google Play disabled for this process: %s", reason)
    return PlayUnavailableError(reason)


def _record_failure(reason: str) -> GooglePlayError:
    """Count an infrastructure failure; return the exception the caller raises.

    Returns ``PlayLookupError`` while below the threshold and
    ``PlayUnavailableError`` once Google Play has been given up on.
    """
    limit = _max_infra_failures()
    with _health_lock:
        _health.failures += 1
        _health.last_error = reason
        failures = _health.failures
        tripped = failures >= limit or _health.unavailable_reason is not None
        if tripped and _health.unavailable_reason is None:
            _health.unavailable_reason = reason
    if tripped:
        logging.warning(
            "Google Play disabled for this process after %d infrastructure "
            "failure(s): %s",
            failures,
            reason,
        )
        return PlayUnavailableError(reason)
    logging.warning(
        "Google Play infrastructure failure (%d/%d): %s", failures, limit, reason
    )
    return PlayLookupError(reason)


def _raise_if_unavailable() -> None:
    with _health_lock:
        reason = _health.unavailable_reason
    if reason:
        raise PlayUnavailableError(f"disabled earlier in this run: {reason}")


# --------------------------------------------------------------------------- #
# Timeout helper
# --------------------------------------------------------------------------- #
def _call_with_timeout(fn: Callable[[], T], timeout: int, what: str) -> T:
    """Run ``fn`` in a daemon thread and give up waiting after ``timeout`` seconds.

    A timed-out call cannot be killed; it is abandoned (daemon thread) so the
    caller is never blocked past the deadline.  ``timeout <= 0`` disables this.
    """
    if timeout <= 0:
        return fn()

    box: dict[str, Any] = {}

    def runner() -> None:
        try:
            box["value"] = fn()
        except BaseException as error:  # re-raised in the caller's thread
            box["error"] = error

    thread = threading.Thread(target=runner, name=f"gplay-{what}", daemon=True)
    thread.start()
    thread.join(timeout)
    if thread.is_alive():
        raise TimeoutError(f"{what} timed out after {timeout}s")
    if "error" in box:
        raise box["error"]
    return box["value"]


def _remember(package: str, current: VersionCandidate) -> None:
    """Persist the versionCode; failing to do so must not lose the identity."""
    try:
        remember_version_code(package, current.name, current.code or "")
    except Exception as error:
        logging.warning(
            "Could not remember version code for %s: %s",
            package,
            type(error).__name__,
        )


# --------------------------------------------------------------------------- #
# gplaydl access
# --------------------------------------------------------------------------- #
def _authenticate(arch: str, dispenser: str | None, email: str | None):
    """Obtain the primary auth token.

    Returns ``None`` when gplaydl yields no token (the caller may fall back to
    device profiles).  An exception is an infrastructure failure.
    """
    try:
        from gplaydl.auth import ensure_auth
    except ImportError as error:
        raise _mark_unavailable(
            f"gplaydl is not importable: {type(error).__name__}"
        ) from None
    try:
        auth = ensure_auth(
            arch=arch,
            dispenser_url=dispenser,
            email=email,
        )
    except Exception as error:
        raise _record_failure(
            f"authentication failed: {type(error).__name__}"
        ) from None
    if auth:
        _record_success()
    return auth


def _current_details(
    package: str,
    arch: str,
    dispenser: str | None,
    email: str | None,
):
    """Fetch current details through the pinned gplaydl implementation."""
    from gplaydl.api import get_details

    auth = _authenticate(arch, dispenser, email)
    return get_details(package, auth) if auth else None


def _profile_details(
    package: str,
    arch: str,
    dispenser: str | None,
    email: str | None,
    stats: dict[str, int] | None = None,
) -> Iterable[Any]:
    """Yield Play details from each priority device profile.

    ``stats["auth_ok"]`` (optional) counts profiles that produced an auth
    token, letting callers tell "no auth anywhere" from "package not found".
    """
    from gplaydl.api import get_details
    from gplaydl.auth import fetch_token_for_profile
    from gplaydl.profiles import get_priority_profiles

    preferred = os.getenv("GPLAYDL_PREFERRED_PROFILE", "").strip() or None
    profiles = ordered_priority_profiles(get_priority_profiles(arch), preferred)
    for key, profile in profiles:
        try:
            auth = fetch_token_for_profile(
                profile,
                dispenser_url=dispenser,
                email=email,
            )
            if auth:
                if stats is not None:
                    stats["auth_ok"] = stats.get("auth_ok", 0) + 1
                yield get_details(package, auth)
        except Exception as error:
            logging.info(
                "Google Play current identity profile %s failed for %s: %s",
                key,
                package,
                type(error).__name__,
            )


def _identity(details: Any, package: str) -> VersionCandidate | None:
    returned_package = str(getattr(details, "package", "") or package).strip()
    name = str(getattr(details, "version_string", "") or "").strip()
    code = str(getattr(details, "version_code", "") or "").strip()
    if returned_package != package or not name or not code.isdigit():
        return None
    return VersionCandidate(name=name, code=code)


def _has_bootstrap_credentials() -> bool:
    email = (
        os.getenv("GPLAYDL_EMAIL", "").strip()
        or os.getenv("GPLAY_EMAIL", "").strip()
    )
    return bool(email and os.getenv("GPLAY_AAS_TOKEN", "").strip())


def _context() -> tuple[str, str | None, str | None] | None:
    """Prepare the shared Google Play metadata authentication context.

    A preconfigured dispenser API key is sufficient. When it is absent, the
    existing email+AAS credentials may bootstrap the ephemeral local dispenser,
    which then publishes its generated key/URL into the process environment.

    Returns ``None`` when no credentials are configured.  Raises
    ``PlayLookupError`` / ``PlayUnavailableError`` when the dispenser cannot be
    brought up; once Google Play has been given up on, this fails immediately
    instead of relaunching the dispenser.
    """
    if not os.getenv("GPLAYDL_API_KEY", "").strip() and not _has_bootstrap_credentials():
        return None

    _raise_if_unavailable()
    try:
        _call_with_timeout(
            local_gplaydl_dispenser.ensure_running,
            _dispenser_start_timeout(),
            "local dispenser startup",
        )
    except Exception as error:
        # local_gplaydl_dispenser redacts credentials from its messages, so the
        # first line is safe to surface (it is the actual reason for the failure).
        detail = (str(error).strip().splitlines() or [""])[0][:200]
        raise _record_failure(
            f"local dispenser startup failed: {type(error).__name__}"
            + (f": {detail}" if detail else "")
        ) from None

    if not os.getenv("GPLAYDL_API_KEY", "").strip():
        # Bootstrap finished without publishing a key: gplaydl cannot proceed.
        raise _record_failure("local dispenser did not publish an API key") from None

    arch = os.getenv("GPLAYDL_ARCH", "arm64").strip() or "arm64"
    dispenser = os.getenv("GPLAYDL_DISPENSER_URL", "").strip() or None
    email = (
        os.getenv("GPLAYDL_EMAIL", "").strip()
        or os.getenv("GPLAY_EMAIL", "").strip()
        or None
    )
    return arch, dispenser, email


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
def _lookup_identity(package: str) -> VersionCandidate | None:
    context = _context()
    if context is None:
        raise PlayNotConfiguredError("no Google Play credentials configured")
    arch, dispenser, email = context

    from gplaydl.api import get_details

    auth = _authenticate(arch, dispenser, email)
    first = None
    if auth:
        try:
            first = get_details(package, auth)
        except Exception as error:
            raise PlayLookupError(
                f"details request failed: {type(error).__name__}"
            ) from None
    current = _identity(first, package) if first is not None else None
    if current is not None:
        _remember(package, current)
        return current

    stats = {"auth_ok": 1 if auth else 0}
    for details in _profile_details(package, arch, dispenser, email, stats=stats):
        current = _identity(details, package)
        if current is not None:
            _record_success()
            _remember(package, current)
            return current

    if not stats["auth_ok"]:
        # Not one profile could even authenticate: infrastructure, not package.
        raise _record_failure("no usable auth token from any device profile") from None
    _record_success()
    return None


def current_release_identity(
    package: str, *, strict: bool = False
) -> VersionCandidate | None:
    """Return Google Play's current package/versionName/versionCode identity.

    The primary account/profile is preferred. Device-profile variants are
    checked only if the primary details response is unavailable or incomplete.
    No APK bytes are downloaded.

    ``strict=False`` (default) never raises and returns ``None`` on any failure.
    ``strict=True`` raises:
      * ``PlayUnavailableError`` - Google Play is unusable; stop calling it.
      * ``PlayLookupError``      - this lookup failed; others may still work.
    and still returns ``None`` when Play answered but the package identity was
    absent or incomplete (not a failure of Google Play itself).
    """
    if not package:
        return None
    try:
        return _call_with_timeout(
            lambda: _lookup_identity(package),
            _lookup_timeout(),
            "Google Play lookup",
        )
    except GooglePlayError as error:
        logging.info(
            "Google Play current identity lookup failed for %s: %s",
            package,
            error,
        )
        if strict:
            raise
    except TimeoutError:
        failure = _record_failure(f"lookup timed out after {_lookup_timeout()}s")
        if strict:
            raise failure from None
    except Exception as error:
        logging.info(
            "Google Play current identity lookup failed for %s: %s",
            package,
            type(error).__name__,
        )
        if strict:
            raise PlayLookupError(
                f"unexpected {type(error).__name__}"
            ) from None
    return None


def resolve_candidate_identities(
    package: str,
    candidates: list[VersionCandidate],
) -> list[VersionCandidate]:
    """Enrich only patch candidates matching a current Play identity exactly."""
    resolved = list(candidates)
    if not package or not resolved:
        return resolved

    try:
        context = _context()
        if context is None:
            return resolved
        arch, dispenser, email = context
        first_details = _current_details(package, arch, dispenser, email)
        if first_details is None:
            logging.info("Google Play current identity lookup returned no auth token")
            return resolved
    except Exception as error:
        logging.info(
            "Google Play current identity lookup failed for %s: %s",
            package,
            type(error).__name__,
        )
        return resolved

    seen: set[tuple[str, str]] = set()
    for details in (first_details,):
        current = _identity(details, package)
        if current is None:
            logging.info("Google Play current identity was incomplete for %s", package)
            continue
        seen.add((current.name, current.code or ""))
        for index, requested in enumerate(candidates):
            if not requested.matches(current.name, current.code):
                continue
            resolved[index] = VersionCandidate(
                name=current.name,
                code=current.code,
                raw=requested.raw,
            )
            _remember(package, current)
            logging.info(
                "✓ Google Play current metadata resolved patch-required Android "
                "identity %s -> %s",
                requested.describe(),
                resolved[index].describe(),
            )

    unresolved = [
        requested
        for index, requested in enumerate(candidates)
        if resolved[index].code is None
    ]
    if unresolved:
        try:
            for details in _profile_details(package, arch, dispenser, email):
                current = _identity(details, package)
                if current is None or (current.name, current.code or "") in seen:
                    continue
                seen.add((current.name, current.code or ""))
                for index, requested in enumerate(candidates):
                    if resolved[index].code is not None:
                        continue
                    if not requested.matches(current.name, current.code):
                        continue
                    resolved[index] = VersionCandidate(
                        name=current.name,
                        code=current.code,
                        raw=requested.raw,
                    )
                    _remember(package, current)
                    logging.info(
                        "✓ Google Play device-profile metadata resolved "
                        "patch-required Android identity %s -> %s",
                        requested.describe(),
                        resolved[index].describe(),
                    )
                if all(candidate.code is not None for candidate in resolved):
                    break
        except Exception as error:
            logging.info(
                "Google Play device-profile identity lookup failed for %s: %s",
                package,
                type(error).__name__,
            )

    still_unresolved = [
        requested
        for index, requested in enumerate(candidates)
        if resolved[index].code is None
    ]
    if still_unresolved and seen:
        observed = ", ".join(f"{name} ({code})" for name, code in sorted(seen))
        logging.info(
            "Google Play current identities for %s did not exactly match the "
            "patch request: %s",
            package,
            observed,
        )
    return resolved
