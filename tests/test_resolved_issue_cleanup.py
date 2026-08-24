import subprocess

from scripts import close_resolved_build_issues as cleanup


def test_report_only_feature_closes_while_unsupported_stays_open(monkeypatch, tmp_path):
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
