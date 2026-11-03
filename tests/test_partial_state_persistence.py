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


if __name__ == "__main__":
    unittest.main()
