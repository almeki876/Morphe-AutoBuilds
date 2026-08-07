"""Exercise live APK version discovery and produce durable health reports."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import providers, utils
from src.versioning import canonical_version


def _apps() -> list[str]:
    config = json.loads(
        (ROOT / "my-patch-config.json").read_text(encoding="utf-8")
    )
    return list(
        dict.fromkeys(item["app_name"] for item in config["patch_list"])
    )


def _safe_error(error: Exception) -> str:
    return f"{type(error).__name__}: {utils.safe_text_for_log(error)}"


def _markdown(report: dict) -> str:
    lines = [
        "# APK provider health",
        "",
        f"Checked: `{report['checked_at']}`",
        "",
        f"Healthy apps: **{report['healthy_apps']}/{report['total_apps']}**",
        "",
        "| App | Result | Working providers | Details |",
        "|---|---:|---|---|",
    ]
    for app in report["apps"]:
        working = ", ".join(
            f"{item['provider']} ({item['version']})"
            for item in app["providers"]
            if item["status"] == "ok"
        ) or "—"
        details = "; ".join(
            f"{item['provider']}: {item.get('error', item['status'])}"
            for item in app["providers"]
            if item["status"] != "ok"
        )
        details = details.replace("|", "\\|")[:1500] or "—"
        status = "OK" if app["healthy"] else "FAILED"
        lines.append(f"| {app['app']} | {status} | {working} | {details} |")
    lines.extend(
        [
            "",
            "A failure means no configured provider could resolve a current version. "
            "A single provider failure is informational when another provider works.",
        ]
    )
    return "\n".join(lines) + "\n"


def run(
    selected_apps: list[str],
    target_successes: int,
    max_providers: int,
    delay: float,
) -> dict:
    app_results: list[dict] = []
    for app in selected_apps:
        attempts: list[dict] = []
        successes = 0
        priority = providers.download_priority(app)[:max_providers]
        for provider in priority:
            attempt = {"provider": provider, "status": "failed"}
            try:
                config = providers.load_config(app, provider)
                if config is None:
                    attempt.update(status="skipped", error="no configuration")
                else:
                    version = providers.MODULES[provider].get_latest_version(
                        app, config
                    )
                    if not version:
                        raise ValueError("provider returned no version")
                    attempt.update(status="ok", version=canonical_version(version))
                    successes += 1
            except Exception as error:
                attempt["error"] = _safe_error(error)
            attempts.append(attempt)
            if successes >= target_successes:
                break
            if delay:
                time.sleep(delay)
        app_results.append(
            {"app": app, "healthy": successes > 0, "providers": attempts}
        )
        print(
            f"{'OK' if successes else 'FAILED'}: {app} "
            f"({successes} provider(s))",
            flush=True,
        )

    healthy = sum(item["healthy"] for item in app_results)
    return {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "healthy": healthy == len(app_results),
        "healthy_apps": healthy,
        "total_apps": len(app_results),
        "apps": app_results,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apps", help="comma-separated app names")
    parser.add_argument("--target-successes", type=int, default=2)
    parser.add_argument("--max-providers", type=int, default=6)
    parser.add_argument("--delay", type=float, default=0.5)
    parser.add_argument("--json-output", type=Path, default=Path("provider-health.json"))
    parser.add_argument(
        "--markdown-output", type=Path, default=Path("provider-health.md")
    )
    args = parser.parse_args()
    logging.getLogger().setLevel(logging.WARNING)
    selected = (
        [item.strip() for item in args.apps.split(",") if item.strip()]
        if args.apps
        else _apps()
    )
    report = run(
        selected,
        max(1, args.target_successes),
        max(1, args.max_providers),
        max(0.0, args.delay),
    )
    args.json_output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    args.markdown_output.write_text(_markdown(report), encoding="utf-8")

    output = os.getenv("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as handle:
            handle.write(f"healthy={str(report['healthy']).lower()}\n")
            handle.write(
                "failed_apps="
                + json.dumps(
                    [item["app"] for item in report["apps"] if not item["healthy"]]
                )
                + "\n"
            )
    return 0 if report["healthy"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
