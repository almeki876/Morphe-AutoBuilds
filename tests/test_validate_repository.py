import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import validate_repository


class WorkflowReferenceValidationTests(unittest.TestCase):
    def _validate(self, workflow_text: str) -> validate_repository.Validation:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workflows = root / ".github" / "workflows"
            workflows.mkdir(parents=True)
            (workflows / "check.yml").write_text(workflow_text, encoding="utf-8")
            result = validate_repository.Validation()
            with mock.patch.object(validate_repository, "ROOT", root):
                validate_repository._validate_workflows(result)
            return result

    def test_missing_python_script_is_rejected(self) -> None:
        result = self._validate("run: python3 scripts/missing.py\n")
        self.assertEqual(len(result.errors), 1)
        error = result.errors[0].replace("\\", "/")
        self.assertIn(".github/workflows/check.yml", error)
        self.assertIn("missing referenced script scripts/missing.py", error)

    def test_missing_dispatched_workflow_is_rejected(self) -> None:
        result = self._validate("run: gh workflow run missing.yml\n")
        self.assertEqual(len(result.errors), 1)
        error = result.errors[0].replace("\\", "/")
        self.assertIn(".github/workflows/check.yml", error)
        self.assertIn("missing dispatched workflow missing.yml", error)

    def test_existing_script_and_workflow_are_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workflows = root / ".github" / "workflows"
            scripts = root / "scripts"
            workflows.mkdir(parents=True)
            scripts.mkdir()
            (scripts / "task.py").write_text("", encoding="utf-8")
            (workflows / "build.yml").write_text("name: build\n", encoding="utf-8")
            (workflows / "check.yml").write_text(
                "run: |\n"
                "  python3 -m scripts.task\n"
                "  gh workflow run build.yml\n",
                encoding="utf-8",
            )
            result = validate_repository.Validation()
            with mock.patch.object(validate_repository, "ROOT", root):
                validate_repository._validate_workflows(result)
            self.assertEqual(result.errors, [])


if __name__ == "__main__":
    unittest.main()
