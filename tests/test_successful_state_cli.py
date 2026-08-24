import subprocess
import sys
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


class SuccessfulStateCliTests(unittest.TestCase):
    def test_close_resolved_issues_direct_script_can_import_repository_packages(self) -> None:
        """Match build.yml's direct invocation without entering the GitHub API path."""
        result = subprocess.run(
            [sys.executable, "scripts/close_resolved_build_issues.py", "--help"],
            cwd=REPOSITORY_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--directory", result.stdout)

    def test_save_successful_state_direct_script_bootstraps_repository_root(self) -> None:
        """Regression coverage for the lazy scripts.* import used during publication."""
        probe = (
            "import runpy, sys; "
            "from pathlib import Path; "
            "root = str(Path.cwd()); "
            "scripts_dir = str(Path.cwd() / 'scripts'); "
            "sys.path[:] = [scripts_dir] + [p for p in sys.path if p not in ('', root, scripts_dir)]; "
            "runpy.run_path('scripts/save_successful_state.py', run_name='successful_state_probe'); "
            "from scripts.generate_direct_download_md import render; "
            "print('successful-state-package-imports-ok')"
        )
        result = subprocess.run(
            [sys.executable, "-c", probe],
            cwd=REPOSITORY_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("successful-state-package-imports-ok", result.stdout)


if __name__ == "__main__":
    unittest.main()
