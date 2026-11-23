import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from scripts import create_release, generate_release_details, virustotal_gate
from scripts import report_virustotal_exclusions


def _vt_payload() -> dict:
    return {
        "results": [
            {
                "file": "youtube-morphe-arm64-v8a.apk",
                "sha256": "a" * 64,
                "verdict": "malicious",
                "malicious": 3,
                "suspicious": 1,
                "permalink": "https://www.virustotal.com/gui/file/aaa",
                "engines": {},
            },
            {
                "file": "gboard-jason-arm64-v8a.apk",
                "sha256": "b" * 64,
                "verdict": "clean",
                "malicious": 0,
                "suspicious": 0,
                "permalink": "https://www.virustotal.com/gui/file/bbb",
                "engines": {},
            },
        ],
        "failures": [],
    }


class ComputeExclusionsTests(unittest.TestCase):
    def test_clean_pair_is_not_excluded(self) -> None:
        exclusions = virustotal_gate.compute_exclusions(
            _vt_payload(), [("gboard", "jason")]
        )
        self.assertEqual(exclusions, [])

    def test_malicious_pair_is_excluded_with_details(self) -> None:
        exclusions = virustotal_gate.compute_exclusions(
            _vt_payload(), [("youtube", "morphe"), ("gboard", "jason")]
        )
        self.assertEqual(len(exclusions), 1)
        exclusion = exclusions[0]
        self.assertEqual(exclusion.app_name, "youtube")
        self.assertEqual(exclusion.verdict, "malicious")
        self.assertEqual(exclusion.malicious, 3)
        self.assertEqual(exclusion.suspicious, 1)
        self.assertEqual(exclusion.base_files, ["youtube-morphe-arm64-v8a.apk"])
        self.assertEqual(exclusion.sha256, ["a" * 64])

    def test_suspicious_verdict_is_excluded(self) -> None:
        payload = _vt_payload()
        payload["results"][0]["verdict"] = "suspicious"
        exclusions = virustotal_gate.compute_exclusions(
            payload, [("youtube", "morphe")]
        )
        self.assertEqual([item.verdict for item in exclusions], ["suspicious"])

    def test_missing_result_is_excluded_as_unknown(self) -> None:
        exclusions = virustotal_gate.compute_exclusions(
            _vt_payload(), [("twitch", "morphe")]
        )
        self.assertEqual(len(exclusions), 1)
        self.assertEqual(exclusions[0].verdict, "unknown")

    def test_scan_failure_is_excluded_as_unknown(self) -> None:
        payload = _vt_payload()
        payload["results"] = [
            item for item in payload["results"] if item["file"].startswith("gboard-")
        ]
        payload["failures"] = ["youtube-morphe-arm64-v8a.apk: analysis timed out"]
        exclusions = virustotal_gate.compute_exclusions(
            payload, [("youtube", "morphe")]
        )
        self.assertEqual(len(exclusions), 1)
        self.assertEqual(exclusions[0].verdict, "unknown")
        self.assertEqual(
            exclusions[0].failures,
            ["youtube-morphe-arm64-v8a.apk: analysis timed out"],
        )

    def test_failure_for_another_pair_does_not_exclude(self) -> None:
        payload = _vt_payload()
        payload["failures"] = ["twitch-morphe-arm64-v8a.apk: upload failed"]
        exclusions = virustotal_gate.compute_exclusions(
            payload, [("gboard", "jason")]
        )
        self.assertEqual(exclusions, [])


def _release_fixture(root: Path) -> dict[str, Path]:
    artifact_root = root / "all-apks"
    release_dir = root / "release-apks"
    report_root = root / "build-results"
    for app, source, apk_name in (
        ("youtube", "morphe", "YouTube.apk"),
        ("gboard", "jason", "Gboard.apk"),
    ):
        artifact = artifact_root / f"apk-{app}-{source}"
        artifact.mkdir(parents=True)
        (artifact / apk_name).write_bytes(b"apk")
        release_dir.mkdir(parents=True, exist_ok=True)
        (release_dir / apk_name).write_bytes(b"apk")
        report_root.mkdir(parents=True, exist_ok=True)
        (report_root / f"{app}-{source}.json").write_text(
            json.dumps(
                {"app_name": app, "source": source, "status": "success"}
            ),
            encoding="utf-8",
        )
    vt_report = root / "virustotal_base_results.json"
    vt_report.write_text(json.dumps(_vt_payload()), encoding="utf-8")
    return {
        "artifact_root": artifact_root,
        "release_dir": release_dir,
        "report_root": report_root,
        "vt_report": vt_report,
        "exclusions": root / "virustotal_exclusions.json",
    }


class EnforceReleaseGateTests(unittest.TestCase):
    def test_flagged_apk_is_pruned_and_recorded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths = _release_fixture(Path(directory))
            exclusions = virustotal_gate.enforce_release_gate(
                vt_report=paths["vt_report"],
                artifact_root=paths["artifact_root"],
                report_root=paths["report_root"],
                release_dir=paths["release_dir"],
                exclusions_path=paths["exclusions"],
                release_tag="2026-test",
            )
            self.assertEqual([item.app_name for item in exclusions], ["youtube"])
            self.assertFalse((paths["release_dir"] / "YouTube.apk").exists())
            self.assertTrue((paths["release_dir"] / "Gboard.apk").exists())
            recorded = virustotal_gate.load_exclusions(paths["exclusions"])
            self.assertEqual(len(recorded), 1)
            self.assertEqual(recorded[0]["apk_files"], ["YouTube.apk"])
            self.assertEqual(recorded[0]["verdict"], "malicious")

    def test_missing_report_excludes_everything(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths = _release_fixture(Path(directory))
            exclusions = virustotal_gate.enforce_release_gate(
                vt_report=Path(directory) / "does-not-exist.json",
                artifact_root=paths["artifact_root"],
                report_root=paths["report_root"],
                release_dir=paths["release_dir"],
                exclusions_path=paths["exclusions"],
            )
            self.assertEqual(len(exclusions), 2)
            self.assertEqual(
                sorted(path.name for path in paths["release_dir"].glob("*.apk")),
                [],
            )


class CreateReleaseGateTests(unittest.TestCase):
    def test_create_release_does_not_upload_flagged_apk(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = _release_fixture(root)
            cache = root / "cache.json"
            with (
                mock.patch.object(create_release, "RELEASE_DIR", paths["release_dir"]),
                mock.patch.object(create_release, "VT_REPORT", paths["vt_report"]),
                mock.patch.object(create_release, "VT_CACHE", cache),
                mock.patch.object(create_release, "ARTIFACT_ROOT", paths["artifact_root"]),
                mock.patch.object(create_release, "REPORT_ROOT", paths["report_root"]),
                mock.patch.object(create_release, "EXCLUSIONS_PATH", paths["exclusions"]),
                mock.patch.object(
                    create_release,
                    "export_cache",
                    side_effect=lambda _report, output: output.write_text("{}\n", encoding="utf-8") or 1,
                ),
                mock.patch.object(create_release, "run") as run,
            ):
                create_release.create_release("2026-test", "owner/repo")

            commands = [call.args[0] for call in run.call_args_list]
            uploads = [
                command
                for command in commands
                if command[:3] == ["gh", "release", "upload"]
            ]
            uploaded_names = {
                Path(command[4]).name
                for command in uploads
                if len(command) > 4
            }
            self.assertIn("Gboard.apk", uploaded_names)
            self.assertNotIn("YouTube.apk", uploaded_names)


class ReleaseDetailsExclusionTests(unittest.TestCase):
    def test_excluded_app_is_listed_as_excluded_not_included(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            build = root / "build"
            build.mkdir()
            for app, source in (("youtube", "morphe"), ("gboard", "jason")):
                (build / f"{app}-{source}.json").write_text(
                    json.dumps(
                        {
                            "app_name": app,
                            "source": source,
                            "status": "success",
                            "version": "1.0",
                            "applied_patches": ["Patch"],
                        }
                    ),
                    encoding="utf-8",
                )
            exclusions_path = root / "virustotal_exclusions.json"
            virustotal_gate.write_exclusions(
                exclusions_path,
                [
                    virustotal_gate.Exclusion(
                        app_name="youtube",
                        source="morphe",
                        verdict="malicious",
                        reason="VirusTotal did not return a clean verdict.",
                        base_files=["youtube-morphe-arm64-v8a.apk"],
                        malicious=3,
                        suspicious=1,
                        sha256=["a" * 64],
                        permalinks=["https://www.virustotal.com/gui/file/aaa"],
                        apk_files=["YouTube.apk"],
                    )
                ],
                release_tag="2026-test",
            )
            args = SimpleNamespace(
                tag="2026-test",
                repository="owner/repo",
                run_url="",
                build_results=build,
                download_results=root / "download",
                base_inputs=root / "base",
                virustotal=root / "vt.json",
                virustotal_exclusions=exclusions_path,
                output_root=root / "release-details",
                release_notes=root / "notes.md",
            )
            release_dir, notes_path = generate_release_details.generate(args)

            notes = notes_path.read_text(encoding="utf-8")
            self.assertIn("## Excluded by VirusTotal scan", notes)
            self.assertIn("YouTube", notes.split("## Excluded by VirusTotal scan")[1])
            included_section = notes.split("## Excluded by VirusTotal scan")[0]
            self.assertNotIn("YouTube", included_section)
            self.assertIn("Gboard", included_section)

            index = (release_dir / "README.md").read_text(encoding="utf-8")
            self.assertIn("VirusTotal により除外されたアプリ", index)
            archived = virustotal_gate.load_exclusions(
                release_dir / "virustotal-exclusions.json"
            )
            self.assertEqual([item["app_name"] for item in archived], ["youtube"])
            self.assertFalse((release_dir / "apps" / "youtube").exists())
            self.assertTrue((release_dir / "apps" / "gboard").exists())


class ReportVirustotalExclusionsTests(unittest.TestCase):
    def test_dry_run_renders_issue_in_partial_failure_style(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "virustotal_exclusions.json"
            virustotal_gate.write_exclusions(
                path,
                [
                    virustotal_gate.Exclusion(
                        app_name="youtube",
                        source="morphe",
                        verdict="malicious",
                        reason="VirusTotal did not return a clean verdict.",
                        base_files=["youtube-morphe-arm64-v8a.apk"],
                        malicious=3,
                        suspicious=1,
                        sha256=["a" * 64],
                        permalinks=["https://www.virustotal.com/gui/file/aaa"],
                        apk_files=["YouTube.apk"],
                    )
                ],
                release_tag="2026-test",
            )
            import io
            from contextlib import redirect_stdout

            buffer = io.StringIO()
            with redirect_stdout(buffer):
                count = report_virustotal_exclusions.report(path, dry_run=True)
            output = buffer.getvalue()

        self.assertEqual(count, 1)
        self.assertIn(
            "[VirusTotal Exclusion] youtube (morphe) - VirusTotal判定によりリリースから除外",
            output,
        )
        self.assertIn("## 概要", output)
        self.assertIn("※このIssueは自動的に作成されました。", output)
        self.assertIn("`malicious`", output)


if __name__ == "__main__":
    unittest.main()
