import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import validate_release_details as validator


class ValidateReleaseDetailsTests(unittest.TestCase):
    def _write_inputs(self, root: Path, applied: list[str]) -> tuple[Path, Path]:
        build = root / "build"
        build.mkdir()
        (build / "gboard-jason.json").write_text(
            json.dumps(
                {
                    "app_name": "gboard",
                    "source": "jason",
                    "status": "success",
                    "applied_patches": applied,
                }
            ),
            encoding="utf-8",
        )
        config = root / "my-patch-config.json"
        config.write_text(
            json.dumps(
                {
                    "patch_list": [
                        {
                            "app_name": "gboard",
                            "source": "adobo",
                            "force_enable": [
                                "Enable OCR feature",
                                "Enable Undo feature",
                            ],
                        },
                        {
                            "app_name": "gboard",
                            "source": "morning-entree",
                            "force_enable": [
                                "Always incognito mode",
                                "Block tracking and analytics",
                                "Change package name",
                            ],
                        },
                    ]
                }
            ),
            encoding="utf-8",
        )
        return build, config

    def test_gboard_three_source_result_passes_when_supplemental_patches_are_applied(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            build, config = self._write_inputs(
                root,
                [
                    "Jason default patch",
                    "Enable Undo feature",
                    "Always incognito mode",
                    "Block tracking and analytics",
                ],
            )
            with (
                mock.patch.object(validator, "BUILD_ROOT", build),
                mock.patch.object(validator, "PATCH_CONFIG", config),
            ):
                validator.validate()

    def test_gboard_three_source_result_fails_when_supplemental_patch_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            build, config = self._write_inputs(
                root,
                [
                    "Jason default patch",
                    "Enable Undo feature",
                    "Always incognito mode",
                ],
            )
            with (
                mock.patch.object(validator, "BUILD_ROOT", build),
                mock.patch.object(validator, "PATCH_CONFIG", config),
            ):
                with self.assertRaisesRegex(
                    RuntimeError, "Block tracking and analytics"
                ):
                    validator.validate()


if __name__ == "__main__":
    unittest.main()
