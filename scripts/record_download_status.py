"""Write the per-matrix APK download status artifact and job summary entry."""

from __future__ import annotations

import os
from pathlib import Path


def tail_text(path: Path, lines: int) -> str:
    if not path.is_file():
        return "Download step did not start."
    content = path.read_text(encoding="utf-8", errors="replace").splitlines()
    return "\n".join(content[-lines:])


def main() -> int:
    app = os.environ["APP_NAME"]
    source = os.environ["SOURCE"]
    source_name = os.environ["SOURCE_NAME"]
    status = os.environ.get("DOWNLOAD_STATUS", "unknown")
    run_url = os.environ["RUN_URL"]

    directory = Path("download-status")
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{app}-{source}-download.txt"
    body = "\n".join(
        [
            f"app={app}",
            f"source={source}",
            f"source_name={source_name}",
            "phase=Download",
            f"status={status}",
            f"run={run_url}",
            "",
            "traceback_or_error_tail<<EOF",
            tail_text(Path("download.log"), 120),
            "EOF",
            "",
        ]
    )
    path.write_text(body, encoding="utf-8")

    summary = os.getenv("GITHUB_STEP_SUMMARY")
    if summary:
        with Path(summary).open("a", encoding="utf-8") as handle:
            handle.write(body)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
