"""Audit GitHub Actions workflow files for repository policy violations.

This is intentionally static and deterministic. Runtime workflow history is inspected
when diagnosing Actions failures, while this guard prevents temporary AI/debug
workflows and retired one-shot migrations from being committed to main.
"""

from __future__ import annotations

from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"

ALLOWED_WORKFLOWS = {
    "build.yml",
    "build-all-apps.yml",
    "check-upstream.yml",
    "close-resolved-build-issues.yml",
    "configuration-check.yml",
    "diagnose-google-play-purchase.yml",
    "health-check.yml",
    "japan-egress-check.yml",
    "publish-release-details.yml",
    "register-google-play.yml",
    "update-direct-download-links.yml",
}

# Historical one-shot/debug workflows that must never return to main.
RETIRED_ONE_SHOT_WORKFLOWS = {
    "apply-patch-result-gating.yml",
    "apply-recommendation-policy-fix.yml",
    "apply-release-details-integration.yml",
    "apply-upstream-policy-once.yml",
    "patch-gplaydl-secret.yml",
    "trigger-release-details-fix.yml",
    "verify-anddea-fix.yml",
    "verify-anddea-observed.yml",
    "publish-virustotal-cache.yml",
}


def main() -> int:
    paths = sorted(
        list(WORKFLOWS.glob("*.yml")) + list(WORKFLOWS.glob("*.yaml")),
        key=lambda path: path.name,
    )
    actual = {path.name for path in paths}
    errors: list[str] = []

    unexpected = sorted(actual - ALLOWED_WORKFLOWS)
    missing = sorted(ALLOWED_WORKFLOWS - actual)
    if unexpected:
        errors.append("unexpected workflow file(s): " + ", ".join(unexpected))
    if missing:
        errors.append("required workflow file(s) missing: " + ", ".join(missing))

    retired_present = sorted(actual & RETIRED_ONE_SHOT_WORKFLOWS)
    if retired_present:
        errors.append("retired one-shot workflow(s) returned: " + ", ".join(retired_present))

    workflow_run_users: list[str] = []
    for path in paths:
        text = path.read_text(encoding="utf-8")
        if path.name.startswith("_"):
            errors.append(f"temporary/hidden workflow filename is forbidden: {path.name}")
        if re.search(r"^name:\s*[\"']?\s*TEMP(?:\b|:)", text, flags=re.IGNORECASE | re.MULTILINE):
            errors.append(f"TEMP workflow name is forbidden: {path.name}")
        if re.search(r"\b(?:temporary|one[- ]?shot)\b", text.split("jobs:", 1)[0], flags=re.IGNORECASE):
            errors.append(f"temporary/one-shot workflow marker is forbidden: {path.name}")
        if "workflow_run:" in text:
            workflow_run_users.append(path.name)

    # Keep cross-workflow completion hooks centralized. At present only the issue
    # lifecycle finalizer needs to run after the primary build completes.
    if workflow_run_users != ["close-resolved-build-issues.yml"]:
        errors.append(
            "workflow_run must be used only by close-resolved-build-issues.yml; found: "
            + (", ".join(workflow_run_users) or "none")
        )

    if errors:
        print("Workflow audit failed:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1

    print(f"Workflow audit passed: {len(paths)} approved workflow files.")
    print("workflow_run owner: close-resolved-build-issues.yml")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
