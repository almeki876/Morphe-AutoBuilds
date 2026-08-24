import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src import upstream_policy


class UpstreamPolicyTests(unittest.TestCase):
    def test_runtime_policy_does_not_mutate_explicit_patch_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / "my-patch-config.json"
            original = {
                "patch_list": [
                    {
                        "app_name": "yuucho-tsucho",
                        "source": "rushiranpise",
                        "options": [],
                        "disable": [],
                        "force_enable": ["Hide ADB status"],
                        "required": ["Hide ADB status"],
                    },
                    {
                        "app_name": "youtube",
                        "source": "revanced-anddea",
                        "options": [
                            {
                                "patch": "Custom branding name for YouTube",
                                "key": "appName",
                                "value": "RVA",
                            }
                        ],
                        "disable": [],
                        "force_enable": ["Custom branding name for YouTube"],
                        "required": ["Custom branding name for YouTube"],
                    },
                ]
            }
            config.write_text(json.dumps(original, ensure_ascii=False, indent=2), encoding="utf-8")

            old_cwd = Path.cwd()
            old_app = os.environ.get("APP_NAME")
            old_source = os.environ.get("SOURCE")
            try:
                os.chdir(root)
                os.environ["APP_NAME"] = "yuucho-tsucho"
                os.environ["SOURCE"] = "rushiranpise"
                with (
                    patch.object(upstream_policy, "_package_for_app", return_value="jp.japanpost.jp-bankbook"),
                    patch.object(upstream_policy, "_patch_has_version_restriction", return_value=True),
                ):
                    upstream_policy.prepare_runtime_policy()
            finally:
                os.chdir(old_cwd)
                if old_app is None:
                    os.environ.pop("APP_NAME", None)
                else:
                    os.environ["APP_NAME"] = old_app
                if old_source is None:
                    os.environ.pop("SOURCE", None)
                else:
                    os.environ["SOURCE"] = old_source

            self.assertEqual(json.loads(config.read_text(encoding="utf-8")), original)

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
