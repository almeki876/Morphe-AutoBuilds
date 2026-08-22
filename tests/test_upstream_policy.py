import json
import os
import tempfile
import unittest
from pathlib import Path

from src import upstream_policy


class UpstreamPolicyTests(unittest.TestCase):
    def test_recommended_patch_names_respect_use_and_package(self) -> None:
        entries = [
            {
                "name": "Recommended",
                "use": True,
                "compatiblePackages": {"com.example.app": ["1.0"]},
            },
            {
                "name": "Not recommended",
                "use": False,
                "compatiblePackages": {"com.example.app": ["1.0"]},
            },
            {
                "name": "Other package",
                "use": True,
                "compatiblePackages": {"com.other.app": ["1.0"]},
            },
        ]
        self.assertEqual(
            upstream_policy.recommended_patch_names(entries, "com.example.app"),
            {"Recommended"},
        )

    def test_local_options_and_force_enable_cannot_promote_nonrecommended_patch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / "my-patch-config.json"
            config.write_text(
                json.dumps(
                    {
                        "patch_list": [
                            {
                                "app_name": "example",
                                "source": "source",
                                "options": [
                                    {"patch": "Recommended", "key": "x", "value": 1},
                                    {"patch": "Not recommended", "key": "x", "value": 2},
                                ],
                                "force_enable": ["Not recommended"],
                                "required": ["Recommended", "Not recommended"],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            upstream_policy._sanitize_patch_config(
                "example",
                "source",
                {"Recommended"},
                config_path=config,
            )
            result = json.loads(config.read_text(encoding="utf-8"))["patch_list"][0]
            self.assertEqual(result["force_enable"], [])
            self.assertEqual(
                [item["patch"] for item in result["options"]],
                ["Recommended"],
            )
            self.assertEqual(result["required"], ["Recommended"])

    def test_missing_recommendation_metadata_ignores_legacy_allowlist(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / "my-patch-config.json"
            config.write_text(
                json.dumps(
                    {
                        "patch_list": [
                            {
                                "app_name": "example",
                                "source": "source",
                                "options": [
                                    {"patch": "Local patch", "key": "x", "value": 1}
                                ],
                                "force_enable": ["Local patch"],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            patches_dir = root / "patches"
            patches_dir.mkdir()
            allowlist = patches_dir / "example-source.txt"
            allowlist.write_text("+ Local patch\n", encoding="utf-8")

            old_cwd = Path.cwd()
            try:
                os.chdir(root)
                upstream_policy._sanitize_patch_config(
                    "example",
                    "source",
                    None,
                    config_path=Path("my-patch-config.json"),
                )
            finally:
                os.chdir(old_cwd)

            result = json.loads(config.read_text(encoding="utf-8"))["patch_list"][0]
            self.assertEqual(result["force_enable"], [])
            self.assertEqual(result["options"], [])
            self.assertFalse(allowlist.exists())

    def test_any_policy_removes_runtime_provider_version_pins(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            apps = Path(tmp) / "apps"
            provider = apps / "provider"
            provider.mkdir(parents=True)
            config = provider / "example.json"
            config.write_text(
                json.dumps(
                    {
                        "package": "com.example.app",
                        "version": "1.2.3",
                        "version_code": "123",
                        "primary": True,
                    }
                ),
                encoding="utf-8",
            )

            upstream_policy._ignore_provider_version_pins_for_any(
                "example",
                apps_root=apps,
            )
            result = json.loads(config.read_text(encoding="utf-8"))
            self.assertNotIn("version", result)
            self.assertNotIn("version_code", result)
            self.assertEqual(result["package"], "com.example.app")
            self.assertTrue(result["primary"])


if __name__ == "__main__":
    unittest.main()
