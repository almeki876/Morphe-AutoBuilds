from pathlib import Path

FIVE_PATCHES = [
    "Enable compact dialog",
    "Hide handle",
    "Enable smooth transition animation",
    "Restore old comments popup panels",
    "Spoof app version",
]


def update_config() -> None:
    path = Path("my-patch-config.json")
    text = path.read_text(encoding="utf-8")
    marker = '      "app_name": "youtube-music",\n      "source": "revanced-anddea",'
    start = text.index(marker)
    end = text.index("\n    },", start)
    block = text[start:end]
    old = '      "disable": []'
    if old not in block:
        raise SystemExit("youtube-music/revanced-anddea disable field not found")
    values = ",".join(f'"{name}"' for name in FIVE_PATCHES)
    block = block.replace(old, f'      "disable": [{values}]', 1)
    path.write_text(text[:start] + block + text[end:], encoding="utf-8")


def update_reporting() -> None:
    path = Path("scripts/report_build_failure.py")
    text = path.read_text(encoding="utf-8")
    old = '''    failures = [
        item for item in (report.get("feature_failures") or [])
        if isinstance(item, dict) and not _report_only_feature_outcome(item)
    ]'''
    new = '''    requested_values = report.get("requested_patches")
    requested: set[str] | None = None
    if isinstance(requested_values, list):
        requested = {
            str(name).strip()
            for name in requested_values
            if str(name).strip()
        }
        required_values = report.get("required_patches") or []
        if isinstance(required_values, list):
            requested.update(
                str(name).strip()
                for name in required_values
                if str(name).strip()
            )

    failures = [
        item for item in (report.get("feature_failures") or [])
        if (
            isinstance(item, dict)
            and not _report_only_feature_outcome(item)
            and (
                requested is None
                or str(item.get("name") or "").strip() in requested
            )
        )
    ]'''
    if old not in text:
        raise SystemExit("feature failure filter marker not found")
    text = text.replace(old, new, 1)
    old_comment = '''# These outcomes are intentionally preserved in Release Details as evidence,
# but they are not actionable feature failures.  Keep unsupported/failed and
# ordinary configuration exclusions in the issue lifecycle.'''
    new_comment = '''# These outcomes are intentionally preserved in Release Details as evidence,
# but they are not actionable feature failures.  When requested_patches metadata
# is available, unsupported/failed outcomes are actionable only for explicit
# repository-owned requested/required patches; upstream defaults are normal.'''
    if old_comment in text:
        text = text.replace(old_comment, new_comment, 1)
    path.write_text(text, encoding="utf-8")


def update_failure_tests() -> None:
    path = Path("tests/test_failure_reporting.py")
    text = path.read_text(encoding="utf-8")
    marker = '\n\nif __name__ == "__main__":\n'
    addition = '''

    def test_recommended_unsupported_patch_is_not_actionable(self) -> None:
        recommended = {
            "name": "Recommended upstream patch",
            "reason": "[unsupported] not supported in this APK version",
        }
        explicit = {
            "name": "Explicit patch",
            "reason": "[unsupported] not supported in this APK version",
        }
        report = {
            "app_name": "example",
            "source": "example-source",
            "requested_patches": ["Explicit patch"],
            "feature_failures": [recommended, explicit],
        }
        self.assertEqual(_feature_failures(report), [explicit])

    def test_required_patch_remains_actionable(self) -> None:
        required = {
            "name": "Required patch",
            "reason": "[unsupported] not supported in this APK version",
        }
        report = {
            "app_name": "example",
            "source": "example-source",
            "requested_patches": [],
            "required_patches": ["Required patch"],
            "feature_failures": [required],
        }
        self.assertEqual(_feature_failures(report), [required])
'''
    if "test_recommended_unsupported_patch_is_not_actionable" not in text:
        if marker not in text:
            raise SystemExit("test insertion marker not found")
        text = text.replace(marker, addition + marker, 1)
    path.write_text(text, encoding="utf-8")


def create_policy_test() -> None:
    path = Path("tests/test_recommended_patch_policy.py")
    path.write_text('''import json\nfrom pathlib import Path\n\n\ndef test_youtube_music_unsupported_recommended_patches_are_disabled():\n    data = json.loads(Path("my-patch-config.json").read_text(encoding="utf-8"))\n    entry = next(\n        item for item in data["patch_list"]\n        if item.get("app_name") == "youtube-music"\n        and item.get("source") == "revanced-anddea"\n    )\n    expected = {\n        "Enable compact dialog",\n        "Hide handle",\n        "Enable smooth transition animation",\n        "Restore old comments popup panels",\n        "Spoof app version",\n    }\n    assert expected <= set(entry.get("disable") or [])\n''', encoding="utf-8")


if __name__ == "__main__":
    update_config()
    update_reporting()
    update_failure_tests()
    create_policy_test()
