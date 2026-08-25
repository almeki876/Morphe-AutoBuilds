"""Inspect Japanese resources in APKs and split APK containers."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import os
import re
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path


class JapaneseResourceStatus(str, Enum):
    PRESENT = "present"
    ABSENT = "absent"
    UNVERIFIED = "unverified"


@dataclass(frozen=True)
class JapaneseResourceInspection:
    status: JapaneseResourceStatus
    detail: str
    evidence: str | None = None


class JapaneseResourceError(ValueError):
    """Backward-compatible error for callers that require a positive result."""


class JapaneseResourceVerificationUnavailable(JapaneseResourceError):
    """Backward-compatible error describing an unavailable inspection."""


_RESOURCE_PATH_RE = re.compile(
    r"(?:^|/)res/values-(?:ja(?:-rjp)?|b\+ja(?:\+jp)?)(?:-[^/]*)?/",
    re.IGNORECASE,
)
_JA_SPLIT_RE = re.compile(
    r"(?:^|/)(?:split[_-]?config[._-]|config[._-])ja(?:[._-]r?jp)?\.apk$",
    re.IGNORECASE,
)
_LOCALE_CONFIG_RE = re.compile(
    r"(?:android:)?name\s*=\s*['\"]ja(?:-JP)?['\"]", re.IGNORECASE
)


def _build_tools_dirs() -> list[Path]:
    roots = [
        Path(value)
        for name in ("ANDROID_HOME", "ANDROID_SDK_ROOT")
        if (value := os.getenv(name))
    ]
    roots.append(Path("/usr/local/lib/android/sdk"))
    directories: list[Path] = []
    for root in roots:
        build_tools = root / "build-tools"
        if build_tools.is_dir():
            directories.extend(
                sorted(
                    (path for path in build_tools.iterdir() if path.is_dir()),
                    reverse=True,
                )
            )
    return directories


def _find_aapt() -> str | None:
    for name in ("aapt2", "aapt"):
        if found := shutil.which(name):
            return found
    names = ("aapt2.exe", "aapt.exe") if os.name == "nt" else ("aapt2", "aapt")
    for directory in _build_tools_dirs():
        for name in names:
            candidate = directory / name
            if candidate.is_file():
                return str(candidate)
    return None


def _resource_path_evidence(names: list[str]) -> str | None:
    for raw_name in names:
        name = raw_name.replace("\\", "/")
        if _RESOURCE_PATH_RE.search(name) or _JA_SPLIT_RE.search(name):
            return name
    return None


def _locale_config_evidence(archive: zipfile.ZipFile) -> str | None:
    candidates = {
        "res/xml/locales_config.xml",
        "res/xml/locale_config.xml",
        "res/xml/locale-config.xml",
    }
    for raw_name in archive.namelist():
        name = raw_name.replace("\\", "/").lower()
        if name not in candidates:
            continue
        try:
            text = archive.read(raw_name).decode("utf-8")
        except (KeyError, UnicodeDecodeError):
            continue
        if _LOCALE_CONFIG_RE.search(text):
            return raw_name
    return None


def _aapt_resource_status(path: Path, aapt: str) -> JapaneseResourceInspection:
    try:
        result = subprocess.run(
            [aapt, "dump", "resources", str(path)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except OSError as error:
        return JapaneseResourceInspection(
            JapaneseResourceStatus.UNVERIFIED,
            f"aapt could not start: {error}",
        )
    if result.returncode != 0:
        diagnostics = (result.stderr or result.stdout or "unknown error").strip()
        return JapaneseResourceInspection(
            JapaneseResourceStatus.UNVERIFIED,
            f"aapt exited with {result.returncode}: {diagnostics[:300]}",
        )
    for line in result.stdout.splitlines():
        normalized = line.strip().lower().replace("_", "-")
        if re.search(r"\bconfig\s+\(?[^\s)]*\bja(?:-r?jp)?(?:\b|$)", normalized):
            return JapaneseResourceInspection(
                JapaneseResourceStatus.PRESENT, "aapt resource configuration", line.strip()
            )
        if re.search(r"\blocale\s+\(?ja(?:-r?jp)?(?:\b|$)", normalized):
            return JapaneseResourceInspection(
                JapaneseResourceStatus.PRESENT, "aapt locale declaration", line.strip()
            )
    return JapaneseResourceInspection(
        JapaneseResourceStatus.ABSENT,
        "aapt completed but no Japanese resource configuration was found",
    )


def _inspect_plain_apk(path: Path, aapt: str | None) -> JapaneseResourceInspection:
    try:
        with zipfile.ZipFile(path) as archive:
            evidence = _resource_path_evidence(
                archive.namelist()
            ) or _locale_config_evidence(archive)
    except (OSError, zipfile.BadZipFile) as error:
        return JapaneseResourceInspection(
            JapaneseResourceStatus.UNVERIFIED, f"APK archive could not be inspected: {error}"
        )
    if evidence:
        return JapaneseResourceInspection(
            JapaneseResourceStatus.PRESENT,
            "Japanese resource path or language split was found",
            evidence,
        )
    if not aapt:
        return JapaneseResourceInspection(
            JapaneseResourceStatus.UNVERIFIED,
            "aapt/aapt2 unavailable and no supplemental Japanese resource path was found",
        )
    return _aapt_resource_status(path, aapt)


def inspect_japanese_resources(path: Path) -> JapaneseResourceInspection:
    """Return present, absent, or unverified without rejecting the APK."""
    aapt = _find_aapt()
    if path.suffix.casefold() == ".apk":
        return _inspect_plain_apk(path, aapt)

    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            evidence = _resource_path_evidence(names)
            nested = [
                (name, archive.read(name))
                for name in names
                if name.casefold().endswith(".apk") and not name.endswith("/")
            ]
    except (OSError, zipfile.BadZipFile) as error:
        return JapaneseResourceInspection(
            JapaneseResourceStatus.UNVERIFIED,
            f"APK container could not be inspected: {error}",
        )
    if evidence:
        return JapaneseResourceInspection(
            JapaneseResourceStatus.PRESENT,
            "Japanese resource path or language split was found",
            evidence,
        )
    if not nested:
        return JapaneseResourceInspection(
            JapaneseResourceStatus.UNVERIFIED, "split container contains no APKs"
        )

    results: list[JapaneseResourceInspection] = []
    with tempfile.TemporaryDirectory(prefix="apk-ja-check-") as directory:
        root = Path(directory)
        for index, (name, payload) in enumerate(nested):
            candidate = root / f"nested-{index}.apk"
            candidate.write_bytes(payload)
            result = _inspect_plain_apk(candidate, aapt)
            if result.status is JapaneseResourceStatus.PRESENT:
                return JapaneseResourceInspection(
                    result.status,
                    result.detail,
                    f"{name}: {result.evidence or 'aapt'}",
                )
            results.append(result)
    unavailable = next(
        (item for item in results if item.status is JapaneseResourceStatus.UNVERIFIED),
        None,
    )
    if unavailable:
        return unavailable
    return JapaneseResourceInspection(
        JapaneseResourceStatus.ABSENT,
        "all nested APKs were inspected; no Japanese resources were found",
    )


def contains_japanese(path: Path) -> bool:
    """Compatibility helper; use inspect_japanese_resources for three-state results."""
    return inspect_japanese_resources(path).status is JapaneseResourceStatus.PRESENT
