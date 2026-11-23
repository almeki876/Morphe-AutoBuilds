"""VirusTotal release gate for the integrated release.

The per-app VirusTotal scans judge the *base* APK (before patching).  Until
now nothing on the release path consumed those verdicts: a base APK flagged
malicious or suspicious, or one whose scan never produced a conclusive
result, was still patched, signed and published.

This module is the single place that turns the merged VirusTotal report
(``virustotal_base_results.json``) into a per-app publish decision:

* an app/source pair is publishable only when every scanned base APK file
  for that pair has verdict ``clean`` and no scan failure was recorded for
  the pair;
* anything else (malicious, suspicious, any other non-clean verdict, a
  recorded scan failure, or no result at all) excludes exactly that pair
  from the integrated release.  The rest of the release proceeds.

The exclusions are written to ``virustotal_exclusions.json`` so the release
notes, the release-details tree and ``scripts/report_virustotal_exclusions.py``
(issue reporting) all describe the same decision.

Standard library only: this runs inside the Create Integrated Release job,
whose Python setup does not install third-party dependencies.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable


EXCLUSIONS_VERSION = 1
DEFAULT_EXCLUSIONS_PATH = Path("virustotal_exclusions.json")


@dataclass
class Exclusion:
    """One app/source pair withheld from the integrated release."""

    app_name: str
    source: str
    verdict: str
    reason: str
    base_files: list[str] = field(default_factory=list)
    malicious: int = 0
    suspicious: int = 0
    sha256: list[str] = field(default_factory=list)
    permalinks: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    apk_files: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def pair_prefix(app_name: str, source: str) -> str:
    """Base-APK file prefix for a pair, matching generate_release_details."""
    return f"{app_name}-{source}-"


def load_report(path: Path) -> dict[str, Any] | None:
    """Return the merged VirusTotal payload, or None when unavailable."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def compute_exclusions(
    payload: dict[str, Any],
    pairs: Iterable[tuple[str, str]],
) -> list[Exclusion]:
    """Decide which of ``pairs`` must be withheld, given a merged VT report."""
    results = payload.get("results")
    if not isinstance(results, list):
        results = []
    failures = payload.get("failures")
    if not isinstance(failures, list):
        failures = []

    exclusions: list[Exclusion] = []
    for app_name, source in pairs:
        prefix = pair_prefix(app_name, source)
        matched = [
            item
            for item in results
            if isinstance(item, dict)
            and str(item.get("file") or "").startswith(prefix)
        ]
        matched_failures = [
            str(item)
            for item in failures
            if isinstance(item, str) and item.startswith(prefix)
        ]
        if not matched and not matched_failures:
            exclusions.append(
                Exclusion(
                    app_name=app_name,
                    source=source,
                    verdict="unknown",
                    reason=(
                        "No VirusTotal scan result was recorded for this "
                        "app's base APK, so it cannot be published as scanned."
                    ),
                )
            )
            continue

        non_clean = [
            item for item in matched if str(item.get("verdict") or "") != "clean"
        ]
        if non_clean or matched_failures:
            verdict = (
                str(non_clean[0].get("verdict") or "unknown")
                if non_clean
                else "unknown"
            )
            reasons: list[str] = []
            if non_clean:
                reasons.append(
                    "VirusTotal did not return a clean verdict for the "
                    f"base APK (verdict: {verdict})."
                )
            if matched_failures:
                reasons.append(
                    "The VirusTotal scan could not be completed for this "
                    "app's base APK."
                )
            exclusions.append(
                Exclusion(
                    app_name=app_name,
                    source=source,
                    verdict=verdict,
                    reason=" ".join(reasons),
                    base_files=[
                        str(item.get("file") or "") for item in matched
                    ],
                    malicious=sum(
                        int(item.get("malicious") or 0) for item in matched
                    ),
                    suspicious=sum(
                        int(item.get("suspicious") or 0) for item in matched
                    ),
                    sha256=[
                        str(item.get("sha256") or "") for item in matched
                    ],
                    permalinks=[
                        str(item.get("permalink") or "") for item in matched
                    ],
                    failures=matched_failures,
                )
            )
    return exclusions


def unknown_exclusions(pairs: Iterable[tuple[str, str]]) -> list[Exclusion]:
    """Exclusions for the case where the merged VT report itself is absent."""
    return [
        Exclusion(
            app_name=app_name,
            source=source,
            verdict="unknown",
            reason=(
                "The merged VirusTotal report was not available in the "
                "release job, so no scan verdict exists for this app."
            ),
        )
        for app_name, source in pairs
    ]


def successful_pairs_from_matrix(
    artifact_root: Path | None = None,
) -> list[tuple[str, str]] | None:
    """Mirror release_notes._successful_release_matrix using EXPECTED_MATRIX.

    Returns None when EXPECTED_MATRIX is not set (outside release CI).
    """
    raw = os.environ.get("EXPECTED_MATRIX", "").strip()
    if not raw:
        return None
    expected = json.loads(raw)
    if not isinstance(expected, list):
        raise ValueError("EXPECTED_MATRIX must be a JSON array")
    root = artifact_root or Path(os.environ.get("ARTIFACT_ROOT", "all-apks"))
    pairs: list[tuple[str, str]] = []
    for item in expected:
        if (
            not isinstance(item, dict)
            or not item.get("app_name")
            or not item.get("source")
        ):
            continue
        artifact = root / f"apk-{item['app_name']}-{item['source']}"
        if artifact.is_dir() and any(artifact.rglob("*.apk")):
            pairs.append((str(item["app_name"]), str(item["source"])))
    return pairs


def successful_pairs_from_reports(report_root: Path) -> list[tuple[str, str]]:
    """Successful (app, source) pairs from per-app build report JSON files."""
    pairs: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    if not report_root.is_dir():
        return pairs
    for path in sorted(report_root.rglob("*.json")):
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(report, dict) or report.get("status") != "success":
            continue
        app = str(report.get("app_name") or "")
        source = str(report.get("source") or "")
        if app and source and (app, source) not in seen:
            seen.add((app, source))
            pairs.append((app, source))
    return pairs


def release_apk_names(
    artifact_root: Path, app_name: str, source: str
) -> list[str]:
    """File names of the built APKs for one pair inside the artifact tree."""
    artifact = artifact_root / f"apk-{app_name}-{source}"
    if not artifact.is_dir():
        return []
    return sorted({path.name for path in artifact.rglob("*.apk")})


def write_exclusions(
    path: Path,
    exclusions: list[Exclusion],
    *,
    release_tag: str = "",
) -> None:
    payload = {
        "version": EXCLUSIONS_VERSION,
        "release_tag": release_tag,
        "exclusions": [item.to_dict() for item in exclusions],
    }
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def load_exclusions(path: Path) -> list[dict[str, Any]]:
    """Read back a written exclusions file; missing file means no exclusions."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(payload, dict):
        return []
    items = payload.get("exclusions")
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, dict)]


def apply_exclusions(
    exclusions: list[Exclusion],
    *,
    artifact_root: Path,
    release_dir: Path,
    exclusions_path: Path = DEFAULT_EXCLUSIONS_PATH,
    release_tag: str = "",
) -> list[Exclusion]:
    """Remove the excluded pairs' APKs from ``release_dir`` and record them."""
    for exclusion in exclusions:
        exclusion.apk_files = release_apk_names(
            artifact_root, exclusion.app_name, exclusion.source
        )
        for name in exclusion.apk_files:
            target = release_dir / name
            if target.is_file():
                target.unlink()
                print(
                    "VirusTotal gate: withheld "
                    f"{exclusion.app_name} ({exclusion.source}) asset {name} "
                    f"(verdict: {exclusion.verdict})",
                    flush=True,
                )
        print(
            "::warning::VirusTotal gate excluded "
            f"{exclusion.app_name} ({exclusion.source}): {exclusion.reason}",
            flush=True,
        )

    write_exclusions(exclusions_path, exclusions, release_tag=release_tag)
    return exclusions


def enforce_release_gate(
    *,
    vt_report: Path,
    artifact_root: Path,
    report_root: Path,
    release_dir: Path,
    exclusions_path: Path = DEFAULT_EXCLUSIONS_PATH,
    release_tag: str = "",
    pairs: list[tuple[str, str]] | None = None,
) -> list[Exclusion]:
    """Apply the gate: prune non-publishable APKs from ``release_dir``.

    The decision is recomputed from the merged VirusTotal report every time
    this runs, so calling it from both the release-notes step and the
    create-release step is idempotent.  When the merged report is missing,
    every built pair is treated as unscanned and withheld (fail closed).
    """
    if pairs is None:
        pairs = successful_pairs_from_reports(report_root)
    payload = load_report(vt_report)
    if payload is None:
        exclusions = unknown_exclusions(pairs)
    else:
        exclusions = compute_exclusions(payload, pairs)
    return apply_exclusions(
        exclusions,
        artifact_root=artifact_root,
        release_dir=release_dir,
        exclusions_path=exclusions_path,
        release_tag=release_tag,
    )
