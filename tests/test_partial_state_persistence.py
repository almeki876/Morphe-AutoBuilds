import tempfile
import unittest
from pathlib import Path

from scripts import save_successful_state as state


def _write(root: Path, app: str, source: str, status: str) -> None:
    (root / f"{app}-{source}-build.txt").write_text(
        f"app={app}\nsource={source}\nsource_name=x\nphase=Patch\nstatus={status}\n"
        "\ntraceback_or_error_tail<<EOF\nstatus=failure appears in a log tail\nEOF\n",
        encoding="utf-8",
    )


class PartialStatePersistenceTests(unittest.TestCase):
    def test_only_failed_sources_and_apps_stay_pending(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, "github", "hoo-dles", "failure")
            _write(root, "youtube", "morphe", "success")
            _write(root, "tiktok", "icysymmetra", "cancelled")
            sources, apps = state.build_outcomes(root)
        self.assertEqual(sources, {"hoo-dles", "icysymmetra"})
        self.assertEqual(apps, {"github", "tiktok"})

    def test_log_tail_text_is_not_parsed_as_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, "youtube", "morphe", "success")
            self.assertEqual(state.build_outcomes(root), (set(), set()))

    def test_gboard_failure_keeps_all_three_sources_pending(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, "gboard", "jason", "failure")
            sources, _ = state.build_outcomes(root)
        self.assertEqual(sources, {"jason", "adobo", "morning-entree"})

    def test_missing_directory_means_no_failures(self) -> None:
        self.assertEqual(state.build_outcomes(Path("/nonexistent-dir")), (set(), set()))

    def test_successful_builds_identify_only_successful_targets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, "youtube", "morphe", "success")
            _write(root, "tiktok", "icysymmetra", "failure")
            sources, apps = state.successful_builds(root)
        self.assertEqual(sources, {"morphe"})
        self.assertEqual(apps, {"youtube"})

    def test_successful_gboard_build_advances_its_integrated_sources(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, "gboard", "jason", "success")
            sources, apps = state.successful_builds(root)
        self.assertEqual(sources, {"jason", "adobo", "morning-entree"})
        self.assertEqual(apps, {"gboard"})

    def test_only_successfully_built_sources_advance_to_resolved_tag(self) -> None:
        current = {"healthy": "v1", "not-built": "v1", "failed": "v1"}
        state.advance_successful_source_tags(
            current,
            {"healthy": "v2", "not-built": "v2", "failed": "v2"},
            {"healthy", "failed"},
            {"failed"},
        )
        self.assertEqual(current, {"healthy": "v2", "not-built": "v1", "failed": "v1"})

    def test_apk_versions_come_only_from_successful_build_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name, app, version in (
                ("youtube", "youtube", "21.5.0"),
                ("failed", "tiktok", "47.0.0"),
                ("not-built", "github", "1.2.0"),
            ):
                artifact = root / name
                artifact.mkdir()
                (artifact / "manifest.json").write_text(
                    f'{{"app_name":"{app}","version":"{version}"}}',
                    encoding="utf-8",
                )
            result = state.successful_apk_versions(
                root,
                {"youtube", "tiktok"},
                {"tiktok"},
            )
        self.assertEqual(result, {"youtube": "21.5.0"})

    def test_conflicting_successful_apk_versions_are_not_advanced(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name, version in (("one", "1.0"), ("two", "2.0")):
                artifact = root / name
                artifact.mkdir()
                (artifact / "manifest.json").write_text(
                    f'{{"app_name":"youtube","version":"{version}"}}',
                    encoding="utf-8",
                )
            result = state.successful_apk_versions(root, {"youtube"}, set())
        self.assertEqual(result, {})


if __name__ == "__main__":
    unittest.main()
