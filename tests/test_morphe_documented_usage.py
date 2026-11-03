"""Morphe CLI usage that follows the upstream documentation.

Reference: MorpheApp/morphe-desktop ``docs/documentation.md``.

* Split bundles (``.apkm``/``.xapk``/``.apks``) are passed to ``patch``; the CLI
  merges the modules before patching.  Merging happens before patching, never
  after.
* ``--result-file`` reports applied/failed patches with the full exception.
* ``--force`` is an explicit opt-out of the app-version check.
* A bundle given as a *relative* path fails inside the CLI, so every path is
  absolute.
"""

import inspect
import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from src import __main__ as build_main
from src.versioning import VersionCandidate


def _zip(path: Path, entries: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    return path


class BundleInputTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_recognised_suffixes_are_kept_and_made_absolute(self) -> None:
        for suffix in (".apkm", ".xapk", ".apks", ".APKM"):
            bundle = _zip(self.root / f"app{suffix}", {"base.apk": b"x"})
            result = build_main._prepare_bundle_input(bundle)
            self.assertTrue(result.is_absolute())
            self.assertEqual(result.suffix.casefold(), suffix.casefold())

    def test_relative_bundle_path_becomes_absolute(self) -> None:
        _zip(self.root / "rel.apkm", {"base.apk": b"x"})
        previous = Path.cwd()
        os.chdir(self.root)
        try:
            result = build_main._prepare_bundle_input(Path("rel.apkm"))
        finally:
            os.chdir(previous)
        self.assertTrue(result.is_absolute())
        self.assertTrue(result.is_file())

    def test_unknown_suffix_with_apk_modules_is_exposed_as_apks(self) -> None:
        bundle = _zip(self.root / "download.bin", {"splits/base.apk": b"x"})
        result = build_main._prepare_bundle_input(bundle)
        self.assertEqual(result.suffix, ".apks")
        self.assertTrue(bundle.is_file())  # the original is untouched

    def test_invalid_containers_are_rejected(self) -> None:
        not_zip = self.root / "bad.bin"
        not_zip.write_bytes(b"not a zip")
        empty = _zip(self.root / "empty.bin", {"readme.txt": b"x"})
        for path in (not_zip, empty):
            with self.assertRaises(build_main.BuildFailure):
                build_main._prepare_bundle_input(path)


class PatchCommandTests(unittest.TestCase):
    def _command(self, *, supports: set[str], force: bool = True, cwd: Path | None = None):
        commands: list[list[str]] = []
        previous = Path.cwd()
        if cwd is not None:
            os.chdir(cwd)
        try:
            with (
                mock.patch.object(build_main, "_log_available_patches"),
                mock.patch.object(
                    build_main.cli_compat, "is_nested_arggroup_syntax", return_value=True
                ),
                mock.patch.object(
                    build_main.cli_compat,
                    "supports_flag",
                    side_effect=lambda _cli, _command, flag: flag in supports,
                ),
                mock.patch.object(
                    build_main.utils,
                    "run_process",
                    side_effect=lambda command, **_kw: commands.append(command),
                ),
            ):
                build_main._patch_morphe(
                    Path("cli.jar"),
                    Path("patches.mpp"),
                    Path("bundle.apkm"),
                    Path("out.apk"),
                    [],
                    [],
                    [],
                    result_file=Path("result.json"),
                    temp_dir=Path("scratch"),
                    force=force,
                )
        finally:
            os.chdir(previous)
        self.assertEqual(len(commands), 1)
        return commands[0]

    def test_documented_flags_and_absolute_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            command = self._command(
                supports={"--unsigned", "--result-file", "--temporary-files-path"},
                cwd=Path(tmp),
            )
            root = Path(tmp).resolve()
        for flag in ("--result-file", "--temporary-files-path"):
            self.assertIn(flag, command)
        value = command[command.index("--result-file") + 1]
        self.assertEqual(Path(value), root / "result.json")
        self.assertEqual(Path(command[command.index("-p") + 1]), root / "patches.mpp")
        self.assertEqual(Path(command[command.index("-jar") + 1]), root / "cli.jar")
        self.assertEqual(Path(command[-1]), root / "bundle.apkm")
        out = next(arg for arg in command if arg.startswith("--out="))
        self.assertTrue(Path(out.split("=", 1)[1]).is_absolute())

    def test_flags_are_omitted_when_the_cli_does_not_offer_them(self) -> None:
        command = self._command(supports=set())
        self.assertNotIn("--result-file", command)
        self.assertNotIn("--temporary-files-path", command)

    def test_force_is_opt_in_per_version(self) -> None:
        self.assertIn("--force", self._command(supports=set(), force=True))
        self.assertNotIn("--force", self._command(supports=set(), force=False))

    def test_metaspace_is_not_capped(self) -> None:
        command = self._command(supports=set())
        self.assertFalse(any("MaxMetaspaceSize" in arg for arg in command))


class ResultFileTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "result.json"
        self.addCleanup(self._tmp.cleanup)

    def test_success_result(self) -> None:
        self.path.write_text(json.dumps({
            "success": True, "appliedPatches": ["A", "B"], "failedPatches": [],
            "stepResults": [{"step": "PATCHING", "success": True}],
        }))
        result = build_main._read_patch_result(self.path)
        self.assertTrue(result.success)
        self.assertEqual(result.applied, ["A", "B"])

    def test_failed_patch_keeps_the_full_exception(self) -> None:
        self.path.write_text(json.dumps({
            "success": True,  # the CLI reports true even when a patch failed
            "appliedPatches": [],
            "failedPatches": [{"name": "Unlock Pro", "error": "PatchException: Failed to match the fingerprint\n\tat x.y"}],
            "stepResults": [{"step": "PATCHING", "success": False}],
        }))
        result = build_main._read_patch_result(self.path)
        self.assertFalse(result.success)
        self.assertEqual(result.failed_steps, ["PATCHING"])
        self.assertIn("fingerprint", result.failed["Unlock Pro"])

    def test_unusable_files_return_none(self) -> None:
        self.assertIsNone(build_main._read_patch_result(self.path))
        self.path.write_text("not json")
        self.assertIsNone(build_main._read_patch_result(self.path))
        self.path.write_text("[]")
        self.assertIsNone(build_main._read_patch_result(self.path))

    def test_result_replaces_log_derived_outcomes_and_names_the_cause(self) -> None:
        parser = build_main.PatchFailureParser()
        parser.adopt_result(build_main.PatchResult(
            success=False, applied=[], failed={"Unlock Pro": "PatchException: Failed to match the fingerprint\n\tat x"},
            failed_steps=["PATCHING"],
        ))
        failure = build_main._semantic_patch_failure(
            parser.result(), [], parser.applied_result(), None, parser.failure_details
        )
        self.assertIsNotNone(failure)
        self.assertIn("Unlock Pro", failure[1])
        self.assertIn("Failed to match the fingerprint", failure[1])

    def test_a_build_that_applied_nothing_is_a_failure(self) -> None:
        failure = build_main._semantic_patch_failure([], [], [], None)
        self.assertIsNotNone(failure)
        self.assertIn("no patch was applied", failure[1])
        self.assertIsNone(build_main._semantic_patch_failure([], [], ["A"], 1))


class VersionPolicyTests(unittest.TestCase):
    def _needs_force(self, candidates, version="13.0.1", is_morphe=True, error=None):
        target = mock.patch.object(
            build_main.utils,
            "get_supported_version_candidates",
            side_effect=error,
            return_value=candidates,
        )
        with target:
            return build_main._needs_force("pkg", Path("c.jar"), Path("p.mpp"), version, is_morphe)

    def test_supported_version_does_not_need_force(self) -> None:
        self.assertFalse(self._needs_force([VersionCandidate(name="13.0.1")]))

    def test_unrestricted_patches_do_not_need_force(self) -> None:
        self.assertFalse(self._needs_force([]))

    def test_undeclared_version_needs_force(self) -> None:
        self.assertTrue(self._needs_force([VersionCandidate(name="12.0.0")]))

    def test_unreadable_declaration_keeps_force(self) -> None:
        self.assertTrue(self._needs_force([], error=RuntimeError("boom")))

    def test_other_clis_keep_force(self) -> None:
        self.assertTrue(self._needs_force([], is_morphe=False))


class PipelineOrderTests(unittest.TestCase):
    def test_bundles_are_never_extracted_or_merged_after_patching(self) -> None:
        source = inspect.getsource(build_main.run_build)
        self.assertNotIn("_extract_split_patch_input", source)
        self.assertNotIn("_merge_split_modules", source)
        self.assertLess(source.index("_prepare_bundle_input("), source.index("_patch_morphe("))
        self.assertLess(
            source.index("_merge_bundle_with_apkeditor("), source.index("_patch_revanced(")
        )


if __name__ == "__main__":
    unittest.main()
