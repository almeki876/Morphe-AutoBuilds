from pathlib import Path
import subprocess

from scripts import close_resolved_build_issues as cleanup
from scripts import save_successful_state as state


def test_cleanup_closes_runtime_skipped_but_keeps_unsupported(monkeypatch, tmp_path):
    report = {
        "app_name": "youtube-music",
        "source": "revanced-anddea",
        "source_name": "revanced-anddea",
        "status": "success",
        "failed_patches": [],
        "required_failures": [],
        "required_patches_satisfied": True,
        "feature_failures": [
            {"name": "Spoof signature", "reason": "[runtime-skipped-default] default"},
            {"name": "Spoof app version", "reason": "[unsupported] not supported"},
        ],
    }
    issues = [
        {
            "number": 1314,
            "title": "[Feature Failure] youtube-music - revanced-anddea - v9.15.51 - Spoof signature",
            "body": "- **Failed patch feature:** `Spoof signature`\n",
        },
        {
            "number": 1319,
            "title": "[Feature Failure] youtube-music - revanced-anddea - v9.15.51 - Spoof app version",
            "body": "- **Failed patch feature:** `Spoof app version`\n",
        },
    ]
    commands = []

    monkeypatch.setattr(cleanup, "_load_reports", lambda _root: [report])
    monkeypatch.setattr(cleanup, "_open_auto_issues", lambda: issues)

    def fake_run(args, **_kwargs):
        commands.append(list(args))
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(cleanup.subprocess, "run", fake_run)

    assert cleanup.close_resolved(tmp_path, "") == 1
    assert [cmd[3] for cmd in commands if cmd[:3] == ["gh", "issue", "close"]] == ["1314"]


def test_direct_download_generator_is_loaded_by_path(monkeypatch, tmp_path):
    output = tmp_path / "direct.md"
    calls = []

    def fake_run_path(path, **_kwargs):
        calls.append(path)
        return {"render": lambda _releases: "# Direct APK Download Links\n"}

    monkeypatch.setattr(state.runpy, "run_path", fake_run_path)
    monkeypatch.setattr(state, "_release_history", lambda: [])

    state._refresh_direct_download_catalog(output)

    assert calls == ["scripts/generate_direct_download_md.py"]
    assert output.read_text(encoding="utf-8") == "# Direct APK Download Links\n"
