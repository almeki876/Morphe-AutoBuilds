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
_BUILD_VERSION_RE = re.compile(r"^(?:v\d+-)?build-\d+(?:[-\w.]*)$", re.IGNORECASE)
_COMPOSITE_NAME_CODE_RE = re.compile(r"^(?P<name>.+)\.(?P<code>\d+)$")
_DISCOVERED_VERSION_CODES: dict[tuple[str, str], str] = {}
_UNRESTRICTED_POLICIES = frozenset({"any", "null"})
_LIST_VERSIONS_HEADINGS = frozenset({"most common compatible versions:"})
_LOG_PREFIXES = ("info:", "warning:", "error:", "usage:")


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

        # A versionCode is the APK's immutable release identifier. Some base
        # APKs extracted from split bundles omit versionName entirely. When we
        # have an exact expected versionCode (configured or learned from a
        # provider), the matching code is sufficient in that narrow case.
        if self.code is not None and not normalized_name:
            return normalized_code == self.code

        # A numeric-only patch CLI value is ambiguous in practice: some patch
        # sources emit Android versionCode (for example Nova's ``88600``), while
        # others emit a numeric versionName (Sleep as Android's ``20260616``).
        # Both are exact manifest identifiers, so accept an exact match against
        # either field instead of assuming every numeric CLI value is a code.
        if self.code is not None and self.name == self.code:
            return normalized_code == self.code or normalized_name == self.name

        if normalized_name in self.aliases(""):
            if self.code is None:
                return True
            # ``raw`` marks identities parsed from patch CLI output. Real build
            # logs show that the leading numeric component in forms such as
            # ``931240252 (5.161.0.931240252)`` and
            # ``2607250000 (7.22.5.2607250000)`` is not necessarily the APK's
            # manifest versionCode. In that case the exact versionName is the
            # empirically verified release identity. Explicit/configured
            # version codes (raw=None) remain strict below.
            if self.raw is not None:
                return True
            return normalized_code == self.code

        # Some APK manifests duplicate the patch CLI display form in
        # versionName, e.g. Nova reports versionName ``88600 (8.8.6)`` and
        # versionCode ``88600``. Accept this only when both exact components
        # agree, rather than weakening general version-name matching.
        if self.code is not None and normalized_code == self.code:
            if normalized_name == f"{self.code} ({self.name})":
                return True

        # Some upstream asset names append versionCode to versionName, e.g.
        # ``21.0.0.40`` while AndroidManifest.xml reports versionName=21.0.0
        # and versionCode=40. Accept only when both components agree, avoiding
        # a broad prefix match that could hide a genuinely wrong APK.
        if self.code is None and normalized_code:
            composite = _COMPOSITE_NAME_CODE_RE.match(self.name)
            if composite:
                return (
                    composite.group("name") == normalized_name
                    and composite.group("code") == normalized_code
                )

        return False

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


class ParsedCandidates(list[VersionCandidate]):
    """Parsed releases plus whether the CLI stated an unrestricted policy.

    ``get_supported_version_candidates`` historically uses truthiness to
    distinguish malformed CLI output from a recognized result. An unrestricted
    policy (``Any``/``null``) legitimately has zero concrete candidates, so a
    plain empty list loses the information that parsing succeeded. Preserve
    list behavior for callers while making a *fully recognized* unrestricted
    result truthy; iteration and length remain empty so downstream code still
    selects the latest available APK.
    """

    def __init__(
        self,
        values: list[VersionCandidate] | None = None,
        *,
        unrestricted: bool = False,
    ) -> None:
        super().__init__(values or [])
        self.unrestricted = unrestricted

    def __bool__(self) -> bool:
        # list does not implement __bool__; sequence truthiness is based on
        # __len__. Use length explicitly while preserving the special truthy
        # state for a recognized unrestricted (Any/null) policy.
        return len(self) > 0 or self.unrestricted


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
    if not value or value.casefold() in _UNRESTRICTED_POLICIES:
        return None
    if value.casefold().startswith(_LOG_PREFIXES):
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

    # Vendor version labels are not guaranteed to begin with a digit. Poweramp,
    # for example, reports ``build-1025-bundle-play``. Its embedded build number
    # is a display/build identifier, not Android versionCode (the corresponding
    # APK uses 1025004). Keep only the exact versionName here; a provider may
    # enrich the candidate with the real manifest versionCode once discovered.
    if _BUILD_VERSION_RE.match(value):
        return VersionCandidate(name=value, raw=line)
    return None


def parse_candidates(output: str) -> ParsedCandidates:
    """Parse compatible releases and retain only a fully recognized Any policy."""
    candidates: list[VersionCandidate] = []
    seen: set[tuple[str, str | None]] = set()
    unrestricted = False
    unrecognized = False

    for line in output.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        value = stripped.casefold()

        if value in _UNRESTRICTED_POLICIES:
            unrestricted = True
            continue

        candidate = parse_candidate(line)
        if candidate is not None:
            key = (candidate.name, candidate.code)
            if key not in seen:
                candidates.append(candidate)
                seen.add(key)
            continue

        # The CLI's human-readable heading is structural output, not a policy
        # value. Log-prefixed lines are likewise metadata. Anything else must
        # remain fail-closed so an upstream format change cannot silently turn
        # into an unrestricted APK selection.
        if value in _LIST_VERSIONS_HEADINGS or value.startswith(_LOG_PREFIXES):
            continue
        unrecognized = True

    return ParsedCandidates(
        candidates,
        unrestricted=unrestricted and not candidates and not unrecognized,
    )


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
