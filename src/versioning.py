"""Version identities shared by patch CLIs and APK providers.

Patch tools and APK sites do not use one universal version identifier. A
single Android release may be shown as a human-readable version name
(``8.8.6``), a version code (``88600``), or a vendor suffix
(``1.21.0-release``). Keeping both name and code prevents provider-specific
configuration from pinning the build to stale identifiers.
"""

from __future__ import annotations

from dataclasses import dataclass
import re


_PATCH_COUNT_RE = re.compile(r"\s+\(\d+\s+patch(?:es)?\)\s*$", re.IGNORECASE)
_CODE_AND_NAME_RE = re.compile(
    r"^(?P<code>\d+)\s+\((?P<name>\d[^()]*)\)(?:\s+.*)?$"
)
_NAME_AND_CODE_RE = re.compile(
    r"^(?P<name>\d[\w.+ -]*?)\((?P<code>\d+)\)\s*$"
)
_DISCOVERED_VERSION_CODES: dict[tuple[str, str], str] = {}


@dataclass(frozen=True)
class VersionCandidate:
    """One Android release as understood by both patches and APK sites."""

    name: str
    code: str | None = None
    raw: str | None = None

    def __post_init__(self) -> None:
        clean_name = self.name.strip()
        clean_code = self.code.strip() if self.code else None
        if not clean_name:
            raise ValueError("version name must not be empty")
        if clean_code and not clean_code.isdigit():
            raise ValueError(f"version code must be numeric: {clean_code!r}")
        object.__setattr__(self, "name", clean_name)
        object.__setattr__(self, "code", clean_code)

    @property
    def canonical(self) -> str:
        """Stable version value used by cache keys, provenance, and filenames."""
        return self.name

    def describe(self) -> str:
        if self.code and self.code != self.name:
            return f"{self.code} ({self.name})"
        return self.name

    def matches(self, name: str, code: str | None = None) -> bool:
        """Return whether a provider result is the same release identity."""
        normalized_name = str(name).strip()
        normalized_code = str(code).strip() if code is not None else None

        # Some patch CLIs report only Android versionCode (for example Nova
        # reports ``88600`` while the APK manifest versionName is ``8.8.6``).
        # In that representation the code is authoritative and requiring the
        # human-readable name to equal the numeric code rejects the right APK.
        if self.code is not None and self.name == self.code:
            return normalized_code == self.code

        if normalized_name not in self.aliases(""):
            return False
        if self.code is None:
            return True
        return normalized_code == self.code

    def aliases(self, provider: str) -> tuple[str, ...]:
        """Return exact-match aliases in the order preferred by a provider."""
        name = self.name
        without_release = re.sub(r"-release$", "", name, flags=re.IGNORECASE)
        names = [without_release, name] if without_release != name else [name]

        if provider == "apkpure" and self.code:
            ordered = [self.code, *names]
        elif provider == "apkcombo" and self.code:
            ordered = [*names, self.code]
        elif provider == "aptoide" and self.code:
            ordered = [*names, self.code]
        else:
            ordered = names
        return tuple(dict.fromkeys(value for value in ordered if value))


def remember_version_code(package: str, version: str, code: str) -> None:
    """Keep a version code discovered while visiting another provider.

    APKMirror exposes Android versionCode in each variant row, while APKPure's
    stable old-version endpoint requires that code. Keeping it for the current
    build lets APKPure take over when APKMirror's final download is blocked.
    """
    clean_package = package.strip()
    clean_version = version.strip()
    clean_code = code.strip()
    if clean_package and clean_version and clean_code.isdigit():
        _DISCOVERED_VERSION_CODES[(clean_package, clean_version)] = clean_code


def discovered_version_code(package: str, version: str) -> str | None:
    return _DISCOVERED_VERSION_CODES.get((package.strip(), version.strip()))


def parse_candidate(line: str) -> VersionCandidate | None:
    """Parse one non-log line from a Morphe/ReVanced list-versions command."""
    value = line.strip()
    if not value or value.casefold() == "any":
        return None
    if value.casefold().startswith(("info:", "warning:", "error:", "usage:")):
        return None

    value = _PATCH_COUNT_RE.sub("", value).strip()

    match = _CODE_AND_NAME_RE.match(value)
    if match:
        return VersionCandidate(
            name=match.group("name").strip(),
            code=match.group("code"),
            raw=line,
        )

    match = _NAME_AND_CODE_RE.match(value)
    if match:
        return VersionCandidate(
            name=match.group("name").strip(),
            code=match.group("code"),
            raw=line,
        )

    if value.isdigit():
        return VersionCandidate(name=value, code=value, raw=line)
    if re.match(r"^\d", value):
        return VersionCandidate(name=value, raw=line)
    return None


def parse_candidates(output: str) -> list[VersionCandidate]:
    """Parse and de-duplicate all compatible releases from CLI output."""
    candidates: list[VersionCandidate] = []
    seen: set[tuple[str, str | None]] = set()
    for line in output.splitlines():
        candidate = parse_candidate(line)
        if candidate is None:
            continue
        key = (candidate.name, candidate.code)
        if key not in seen:
            candidates.append(candidate)
            seen.add(key)
    return candidates


def canonical_version(value: object) -> str:
    """Normalize provider-specific display text for stable update comparison."""
    text = str(value).strip()
    candidate = parse_candidate(text)
    return candidate.canonical if candidate else text


def pinned_candidate(config: dict) -> VersionCandidate | None:
    """Build a candidate from optional provider configuration pins."""
    name = str(config.get("version") or "").strip()
    code = str(config.get("version_code") or "").strip() or None
    if not name and code:
        name = code
    if not name:
        return None
    return VersionCandidate(name=name, code=code)


def configured_fallback_candidates(config: dict) -> list[VersionCandidate]:
    """Read explicitly approved older APK versions from provider config."""
    raw_values = config.get("fallback_versions") or []
    if not isinstance(raw_values, list):
        raise ValueError("fallback_versions must be a list")

    candidates: list[VersionCandidate] = []
    for value in raw_values:
        if isinstance(value, str):
            candidates.append(VersionCandidate(name=value))
        elif isinstance(value, dict) and value.get("name"):
            candidates.append(
                VersionCandidate(
                    name=str(value["name"]),
                    code=str(value["code"]) if value.get("code") else None,
                )
            )
        else:
            raise ValueError(
                "fallback_versions entries must be version strings or "
                "objects with a name"
            )
    return candidates
