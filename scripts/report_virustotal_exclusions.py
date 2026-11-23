"""Create or update one GitHub issue per VirusTotal-excluded app.

Reads ``virustotal_exclusions.json`` (produced by the VirusTotal release
gate in the create-release job, and archived per release under
``release-details/<tag>/virustotal-exclusions.json``) and files one tracking
issue per excluded app/source pair, in the same style as the partial patch
failure reports from ``scripts/report_build_failure.py``.

Usage:
    python3 scripts/report_virustotal_exclusions.py \
        --file release-details/<tag>/virustotal-exclusions.json [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from scripts import report_build_failure, virustotal_gate


def _issue_title(app: str, source: str) -> str:
    return f"[VirusTotal Exclusion] {app} ({source}) - VirusTotal判定によりリリースから除外"


def _issue_body(exclusion: dict, release_tag: str) -> str:
    app = str(exclusion.get("app_name") or "unknown")
    source = str(exclusion.get("source") or "unknown")
    package = report_build_failure._configured_package(app) or "unknown"
    source_url = report_build_failure._patch_source_url(source)
    base_files = [str(name) for name in exclusion.get("base_files") or []]
    base_lines = "\n".join(f"  - `{name}`" for name in base_files) or "  - (記録なし)"
    sha256 = [str(value) for value in exclusion.get("sha256") or []]
    sha_lines = "\n".join(f"  - `{value}`" for value in sha256) or "  - (記録なし)"
    permalinks = [str(value) for value in exclusion.get("permalinks") or []]
    permalink_lines = (
        "\n".join(f"  - {value}" for value in permalinks) or "  - (記録なし)"
    )
    apk_files = [str(name) for name in exclusion.get("apk_files") or []]
    apk_lines = "\n".join(f"  - `{name}`" for name in apk_files) or "  - (記録なし)"
    failures = [str(value) for value in exclusion.get("failures") or []]
    failure_section = ""
    if failures:
        failure_lines = "\n".join(f"  - {value}" for value in failures)
        failure_section = f"\n- **スキャン失敗の記録**:\n{failure_lines}\n"
    return f"""## 概要
ビルドは完了しましたが、ベースAPKのVirusTotalスキャンでクリーンな判定が得られなかったため、このアプリは統合リリースから除外されました。配布元のAPKまたはパッチソースの対応状況を確認してください。

- **対象アプリ**: {app} ({package})
- **パッチソース**: {source_url}
- **VirusTotal判定**: `{exclusion.get('verdict') or 'unknown'}`（malicious: {int(exclusion.get('malicious') or 0)} / suspicious: {int(exclusion.get('suspicious') or 0)}）
- **判定理由**: {exclusion.get('reason') or 'unknown'}
- **対象のベースAPK**:
{base_lines}
- **SHA-256**:
{sha_lines}
- **VirusTotalレポート**:
{permalink_lines}
- **除外されたリリース資産**:
{apk_lines}
- **リリース**: {release_tag or 'unknown'}
{failure_section}
※このIssueは自動的に作成されました。
"""


def report(exclusions_path: Path, *, dry_run: bool = False) -> int:
    exclusions = virustotal_gate.load_exclusions(exclusions_path)
    if not exclusions:
        print(f"No VirusTotal exclusions recorded in {exclusions_path}.")
        return 0
    try:
        payload = json.loads(exclusions_path.read_text(encoding="utf-8"))
        release_tag = (
            str(payload.get("release_tag") or "") if isinstance(payload, dict) else ""
        )
    except (OSError, ValueError):
        release_tag = ""

    report_build_failure.DRY_RUN = dry_run
    for exclusion in exclusions:
        app = str(exclusion.get("app_name") or "unknown")
        source = str(exclusion.get("source") or "unknown")
        report_build_failure._publish(
            _issue_title(app, source),
            _issue_body(exclusion, release_tag),
        )
    print(f"Processed {len(exclusions)} VirusTotal exclusion(s).")
    return len(exclusions)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--file",
        type=Path,
        default=virustotal_gate.DEFAULT_EXCLUSIONS_PATH,
        help="Path to virustotal_exclusions.json",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Render issue updates without calling GitHub CLI.",
    )
    args = parser.parse_args()
    report(args.file, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
