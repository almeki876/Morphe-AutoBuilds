import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src import gboard_multi


class GboardMultiSourceTests(unittest.TestCase):
    def test_conflicting_supplemental_patches_are_suppressed(self):
        self.assertEqual(
            gboard_multi._effective_selection("adobo"),
            {"Enable Undo feature"},
        )
        self.assertEqual(
            gboard_multi._effective_selection("morning-entree"),
            {"Always incognito mode", "Block tracking and analytics"},
        )

    def test_restrict_bundle_disables_everything_except_requested(self):
        entry = {
            "patches": {
                "Wanted": {"enabled": False, "options": {}},
                "Default extra": {"enabled": True, "options": {}},
            }
        }
        gboard_multi._restrict_bundle(entry, {"Wanted"}, "example")
        self.assertTrue(entry["patches"]["Wanted"]["enabled"])
        self.assertFalse(entry["patches"]["Default extra"]["enabled"])

    def test_restrict_bundle_fails_when_requested_patch_disappears(self):
        entry = {"patches": {"Still here": {"enabled": True}}}
        with self.assertRaisesRegex(RuntimeError, "no longer exist"):
            gboard_multi._restrict_bundle(entry, {"Removed"}, "example")

    def test_patch_command_loads_three_bundles_without_global_exclusive(self):
        command = [
            "java", "-Xmx6g", "-jar", "morphe.jar", "patch",
            "--force", "--continue-on-error",
            "-p", "jason.mpp",
            "--out=out.apk", "--bytecode-mode=STRIP_FAST",
            "--exclusive", "-e", "Some Jason patch",
            "input.apk",
        ]
        with mock.patch.dict(
            os.environ, {"APP_NAME": "gboard", "SOURCE": "jason"}, clear=False
        ), mock.patch.object(
            gboard_multi,
            "_find_bundle",
            side_effect=lambda source: Path(f"tools/{source}/{source}.mpp"),
        ), mock.patch.object(
            gboard_multi,
            "_create_options_file",
            return_value=Path("/tmp/gboard-options.json"),
        ):
            rewritten = gboard_multi.prepare_morphe_command(command)

        self.assertNotIn("--exclusive", rewritten)
        self.assertIn("tools/adobo/adobo.mpp", rewritten)
        self.assertIn("tools/morning-entree/morning-entree.mpp", rewritten)
        self.assertEqual(rewritten.count("-p"), 3)
        options_index = rewritten.index("--options-file")
        self.assertEqual(rewritten[options_index + 1], "/tmp/gboard-options.json")
        self.assertEqual(rewritten[-1], "input.apk")

    def test_adobo_update_collapses_to_one_integrated_gboard_matrix_item(self):
        with tempfile.NamedTemporaryFile(mode="r+", delete=False) as output:
            output_path = output.name
        self.addCleanup(lambda: Path(output_path).unlink(missing_ok=True))

        env = os.environ.copy()
        env.update(
            {
                "GITHUB_OUTPUT": output_path,
                "UPDATED_SOURCES": "adobo",
                "BUILD_ALL_SOURCES": "false",
            }
        )
        result = subprocess.run(
            [sys.executable, "scripts/prepare_matrix.py"],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        line = Path(output_path).read_text(encoding="utf-8").strip()
        self.assertTrue(line.startswith("matrix="))
        matrix = json.loads(line.removeprefix("matrix="))
        gboard_items = [item for item in matrix if item.get("app_name") == "gboard"]
        self.assertEqual(len(gboard_items), 1)
        self.assertEqual(gboard_items[0]["source"], "jason")
        self.assertEqual(
            gboard_items[0]["patch_sources"],
            ["jason", "adobo", "morning-entree"],
        )


if __name__ == "__main__":
    unittest.main()
