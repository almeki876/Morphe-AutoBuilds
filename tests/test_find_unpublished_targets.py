from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import find_unpublished_targets as fut  # noqa: E402


def _load(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


BASE = "https://github.com/o/r/releases/download/2026.11.15.1004/"


def _catalog(*names: str) -> str:
    return "\n".join(f"[x]({BASE}{name})" for name in names)


def _item(app: str, source: str, **extra) -> dict:
    return {"app_name": app, "source": source, "disable": [], **extra}


class FindUnpublishedTests(unittest.TestCase):
    def test_asset_pattern_stays_in_sync_with_catalog_generator(self) -> None:
        generator = _load("generate_direct_download_md", "scripts/generate_direct_download_md.py")
        self.assertEqual(fut.ASSET_RE.pattern, generator.ASSET_RE.pattern)

    def test_published_pairs_are_parsed_from_asset_names(self) -> None:
        pairs = fut.published_targets(
            _catalog(
                "poweramp-arm64-v8a-hoomans-vbuild-1025-bundle-play.apk",
                "smart_launcher-universal-hoo-dles-v6.6.build.020.apk",
            )
        )
        self.assertEqual(
            pairs, {("poweramp", "hoomans"), ("smart_launcher", "hoo-dles")}
        )

    def test_new_app_is_detected(self) -> None:
        catalog = _catalog("poweramp-arm64-v8a-mwbra130-vbuild-1031.apk")
        config = [_item("poweramp", "mwbra130"), _item("musicolet", "hxreborn")]
        state = {"apk_poweramp": "build-1031"}
        self.assertEqual(
            fut.find_unpublished(config, catalog, state), [("musicolet", "hxreborn")]
        )

    def test_source_switch_is_detected_even_though_the_app_was_built_before(self) -> None:
        catalog = _catalog("poweramp-arm64-v8a-hoomans-vbuild-1025-bundle-play.apk")
        config = [_item("poweramp", "mwbra130")]
        state = {"apk_poweramp": "build-1025-bundle-play"}
        self.assertEqual(
            fut.find_unpublished(config, catalog, state), [("poweramp", "mwbra130")]
        )

    def test_built_app_missing_from_catalog_is_left_alone(self) -> None:
        # Gboard's chained multi-source build never appears per source.
        catalog = _catalog("poweramp-arm64-v8a-mwbra130-vbuild-1031.apk")
        config = [
            _item("poweramp", "mwbra130"),
            _item("gboard", "jason"),
            _item("gboard", "adobo"),
        ]
        state = {"apk_poweramp": "x", "apk_gboard": "y"}
        self.assertEqual(fut.find_unpublished(config, catalog, state), [])

    def test_disabled_and_skipped_targets_are_ignored(self) -> None:
        catalog = _catalog("poweramp-arm64-v8a-mwbra130-vbuild-1031.apk")
        config = [
            _item("poweramp", "mwbra130"),
            _item("old", "x", enabled=False),
            _item("paused", "x", skip_build=True),
        ]
        self.assertEqual(fut.find_unpublished(config, catalog, {}), [])


class UnpublishedAppsGuardTests(unittest.TestCase):
    def _run(self, config, catalog, state):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "c.json").write_text(json.dumps({"patch_list": config}), encoding="utf-8")
            (root / "s.json").write_text(json.dumps(state), encoding="utf-8")
            if catalog is not None:
                (root / "d.md").write_text(catalog, encoding="utf-8")
            return fut.unpublished_apps(root / "c.json", root / "d.md", root / "s.json")

    def test_returns_sorted_unique_apps_with_notes(self) -> None:
        catalog = _catalog("a-arm64-v8a-s1-v1.apk")
        apps, notes = self._run(
            [_item("a", "s1"), _item("zeta", "s2"), _item("beta", "s2"), _item("beta", "s3")],
            catalog,
            {"apk_a": "1"},
        )
        self.assertEqual(apps, ["beta", "zeta"])
        self.assertEqual(len(notes), 3)

    def test_missing_or_empty_catalog_builds_nothing(self) -> None:
        self.assertEqual(self._run([_item("a", "s")], None, {})[0], [])
        apps, notes = self._run([_item("a", "s")], "no links here", {})
        self.assertEqual(apps, [])
        self.assertTrue(notes)

    def test_too_many_unpublished_targets_is_treated_as_a_broken_catalog(self) -> None:
        config = [_item(f"app{i}", "s") for i in range(fut.MAX_AUTO_TARGETS + 1)]
        apps, notes = self._run(config, _catalog("other-arm64-v8a-s-v1.apk"), {})
        self.assertEqual(apps, [])
        self.assertIn("refusing", notes[0])

    def test_unreadable_state_builds_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "c.json").write_text(json.dumps({"patch_list": [_item("a", "s")]}), encoding="utf-8")
            (root / "d.md").write_text(_catalog("b-arm64-v8a-s-v1.apk"), encoding="utf-8")
            (root / "s.json").write_text("{broken", encoding="utf-8")
            self.assertEqual(fut.unpublished_apps(root / "c.json", root / "d.md", root / "s.json")[0], [])


class CheckApkVersionsOutputTests(unittest.TestCase):
    def test_new_apps_start_the_build_without_being_reported_as_apk_updates(self) -> None:
        module = _load("check_apk_versions", "scripts/check_apk_versions.py")
        cwd = os.getcwd()
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out.txt"
            os.chdir(tmp)
            os.environ["GITHUB_OUTPUT"] = str(out)
            try:
                module.write_outputs(
                    ["brave"], {"brave": True}, [], True, ["brave"], {}, False,
                    ["musicolet", "poweramp"],
                )
            finally:
                os.chdir(cwd)
                os.environ.pop("GITHUB_OUTPUT", None)
            values = dict(line.split("=", 1) for line in out.read_text(encoding="utf-8").splitlines() if "=" in line)
        self.assertEqual(values["any_apk_updated"], "true")
        self.assertEqual(values["apk_updated_apps"], '["brave"]')
        self.assertEqual(values["updated_apps"], "brave,musicolet,poweramp")

    def test_new_apps_alone_are_enough_to_trigger_the_dispatch(self) -> None:
        module = _load("check_apk_versions", "scripts/check_apk_versions.py")
        cwd = os.getcwd()
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out.txt"
            os.chdir(tmp)
            os.environ["GITHUB_OUTPUT"] = str(out)
            try:
                module.write_outputs([], {}, [], False, [], {}, False, ["poweramp"])
            finally:
                os.chdir(cwd)
                os.environ.pop("GITHUB_OUTPUT", None)
            values = dict(line.split("=", 1) for line in out.read_text(encoding="utf-8").splitlines() if "=" in line)
        self.assertEqual(values["any_apk_updated"], "true")
        self.assertEqual(values["apk_updated_apps"], "[]")
        self.assertEqual(values["updated_apps"], "poweramp")


if __name__ == "__main__":
    unittest.main()
