import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


class RunLoggedCliTests(unittest.TestCase):
    def test_direct_script_mode_can_load_morphe_fallback_helper(self) -> None:
        """Match build.yml's ``python3 scripts/run_logged.py`` execution mode."""
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "build.log"
            env = dict(os.environ)
            env["SOURCE"] = "morphe"
            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/run_logged.py",
                    "--output",
                    str(log_path),
                    "--",
                    sys.executable,
                    "-c",
                    "print('script-mode-ok')",
                ],
                cwd=REPOSITORY_ROOT,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("script-mode-ok", result.stdout)
            self.assertIn("script-mode-ok", log_path.read_text(encoding="utf-8"))

    def test_direct_script_loader_exposes_lazy_repository_packages(self) -> None:
        """Regression coverage for Run #560's helper-internal package imports."""
        probe = (
            "import runpy; "
            "ns = runpy.run_path('scripts/run_logged.py', run_name='run_logged_direct_probe'); "
            "ns['_load_morphe_toolchain_fallback'](); "
            "from scripts.download_all_tools import download_asset; "
            "from src import utils; "
            "print('nested-package-imports-ok')"
        )
        result = subprocess.run(
            [sys.executable, "-c", probe],
            cwd=REPOSITORY_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("nested-package-imports-ok", result.stdout)


if __name__ == "__main__":
    unittest.main()
