from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parent.parent


class PrepareMatrixTests(unittest.TestCase):
    def _run(self, **env_values: str) -> list[dict]:
        with tempfile.NamedTemporaryFile(delete=False) as output_file:
            output_path = output_file.name
        try:
            env = os.environ.copy()
            env.update(
                {
                    "GITHUB_OUTPUT": output_path,
                    "BUILD_ALL_SOURCES": "false",
                    "UPDATED_SOURCES": "",
                    "UPDATED_APPS": "",
                }
            )
            env.update(env_values)
            subprocess.run(
                [sys.executable, "scripts/prepare_matrix.py"],
                cwd=ROOT,
                env=env,
                check=True,
                capture_output=True,
                text=True,
            )
            line = Path(output_path).read_text(encoding="utf-8").strip()
            self.assertTrue(line.startswith("matrix="), line)
            return json.loads(line.removeprefix("matrix="))
        finally:
            Path(output_path).unlink(missing_ok=True)

    def test_source_and_app_updates_are_unioned(self) -> None:
        matrix = self._run(
            UPDATED_SOURCES="adobo",
            UPDATED_APPS="amazon-shopping",
        )
        pairs = {(item["app_name"], item["source"]) for item in matrix}
        self.assertIn(("amazon-shopping", "rushiranpise"), pairs)
        self.assertIn(("gboard", "jason"), pairs)

    def test_gboard_sources_collapse_to_one_integrated_target(self) -> None:
        matrix = self._run(UPDATED_SOURCES="adobo,morning-entree")
        gboard = [item for item in matrix if item["app_name"] == "gboard"]
        self.assertEqual(len(gboard), 1)
        self.assertEqual(gboard[0]["source"], "jason")
        self.assertEqual(
            gboard[0]["patch_sources"],
            ["jason", "adobo", "morning-entree"],
        )


if __name__ == "__main__":
    unittest.main()
