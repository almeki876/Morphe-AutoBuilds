from __future__ import annotations

import inspect
import io
import json
import os
import struct
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from scripts import download_all_tools
from src import __main__ as build_main
from src import utils


ROOT = Path(__file__).resolve().parents[1]


class SignedPatchInputTests(unittest.TestCase):
    def test_status_output_cannot_fail_a_build_on_cp932_windows_console(self) -> None:
        raw = io.BytesIO()
        console = io.TextIOWrapper(raw, encoding="cp932")
        with mock.patch.object(sys, "stdout", console):
            build_main._console_print("✅ APK built")
            console.flush()
        self.assertEqual(raw.getvalue().decode("cp932").strip(), "? APK built")

    def test_split_bundle_extracts_stock_base_without_rewriting_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle = root / "input.apkm"
            stock_base = b"signed-stock-base"
            with zipfile.ZipFile(bundle, "w") as archive:
                archive.writestr("split_config.arm64_v8a.apk", b"native-split")
                archive.writestr("base.apk", stock_base)

            previous = Path.cwd()
            os.chdir(root)
            try:
                base, modules = build_main._extract_split_patch_input(
                    bundle, "example", "1.0"
                )
                self.assertEqual(base.read_bytes(), stock_base)
                self.assertEqual(
                    (modules / "split_config.arm64_v8a.apk").read_bytes(),
                    b"native-split",
                )
            finally:
                os.chdir(previous)

    def test_split_bundle_rejects_parent_path_module(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle = root / "input.apkm"
            with zipfile.ZipFile(bundle, "w") as archive:
                archive.writestr("../base.apk", b"unsafe")

            previous = Path.cwd()
            os.chdir(root)
            try:
                with self.assertRaises(build_main.BuildFailure):
                    build_main._extract_split_patch_input(bundle, "example", "1.0")
                self.assertFalse((root / "base.apk").exists())
            finally:
                os.chdir(previous)

    def test_split_merge_uses_patched_module_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            modules = root / "modules"
            modules.mkdir()
            output = root / "merged.apk"

            def run_process(command, **_kwargs):
                self.assertEqual(command[command.index("-i") + 1], str(modules))
                self.assertEqual(command[command.index("-o") + 1], str(output))
                output.write_bytes(b"merged")

            with (
                mock.patch.object(
                    build_main.downloader,
                    "download_apkeditor",
                    return_value=Path("APKEditor.jar"),
                ),
                mock.patch.object(
                    build_main.utils,
                    "run_process",
                    side_effect=run_process,
                ),
            ):
                build_main._merge_split_modules(modules, output, "example")

            self.assertEqual(output.read_bytes(), b"merged")

    def test_split_dependent_failure_is_retried_only_when_file_exists(self) -> None:
        parser = build_main.PatchFailureParser()
        parser("SEVERE: FAILED: Enable Prime membership\n")
        parser(
            "PatchException: /tmp/patching/apk/root/"
            "lib/arm64-v8a/libibispaint.so (No such file or directory)\n"
        )

        with tempfile.TemporaryDirectory() as directory:
            modules = Path(directory)
            with zipfile.ZipFile(modules / "split_config.arm64_v8a.apk", "w") as archive:
                archive.writestr("lib/arm64-v8a/libibispaint.so", b"native")

            self.assertEqual(
                build_main._split_dependent_failures(parser, modules),
                ["Enable Prime membership"],
            )

    def test_unrelated_patch_failure_is_not_retried_after_split_merge(self) -> None:
        parser = build_main.PatchFailureParser()
        parser("SEVERE: FAILED: Fingerprint mismatch\n")
        parser("PatchException: Failed to match the fingerprint\n")

        with tempfile.TemporaryDirectory() as directory:
            modules = Path(directory)
            with zipfile.ZipFile(modules / "base.apk", "w") as archive:
                archive.writestr("classes.dex", b"dex")

            self.assertEqual(build_main._split_dependent_failures(parser, modules), [])

    def test_split_retry_matches_abi_placeholder_from_upstream_error(self) -> None:
        parser = build_main.PatchFailureParser()
        parser("SEVERE: FAILED: Unlock Pro\n")
        parser(
            "PatchException: No lib/<abi>/libisvideoengine.so found in the APK.\n"
        )

        with tempfile.TemporaryDirectory() as directory:
            modules = Path(directory)
            with zipfile.ZipFile(modules / "config.arm64_v8a.apk", "w") as archive:
                archive.writestr("lib/arm64-v8a/libisvideoengine.so", b"native")

            self.assertEqual(
                build_main._split_dependent_failures(parser, modules),
                ["Unlock Pro"],
            )

    def test_split_retry_matches_explicit_not_found_library(self) -> None:
        parser = build_main.PatchFailureParser()
        parser("SEVERE: FAILED: Unlock Premium\n")
        parser(
            "PatchException: lib/arm64-v8a/libpowerampcore.so not found in the APK.\n"
        )

        with tempfile.TemporaryDirectory() as directory:
            modules = Path(directory)
            with zipfile.ZipFile(modules / "arm64.apk", "w") as archive:
                archive.writestr("lib/arm64-v8a/libpowerampcore.so", b"native")

            self.assertEqual(
                build_main._split_dependent_failures(parser, modules),
                ["Unlock Premium"],
            )

    def test_architecture_filtering_happens_after_patching(self) -> None:
        source = inspect.getsource(build_main.run_build)
        patch_call = source.index("_patch_morphe(")
        strip_call = source.index("_strip_libs(output_apk, arch)")
        self.assertLess(patch_call, strip_call)
        self.assertNotIn("_strip_libs(input_apk, arch)", source)

    def test_morphe_patching_defers_signing_to_the_final_pipeline_step(self) -> None:
        commands: list[list[str]] = []
        with (
            mock.patch.object(build_main, "_log_available_patches"),
            mock.patch.object(
                build_main.cli_compat,
                "is_nested_arggroup_syntax",
                return_value=True,
            ),
            mock.patch.object(
                build_main.cli_compat,
                "supports_flag",
                side_effect=lambda _cli, _command, flag: flag == "--unsigned",
            ),
            mock.patch.object(
                build_main.utils,
                "run_process",
                side_effect=lambda command, **_kwargs: commands.append(command),
            ),
        ):
            build_main._patch_morphe(
                Path("morphe.jar"),
                Path("patches.mpp"),
                Path("input.apk"),
                Path("output.apk"),
                [],
                [],
                [],
            )

        self.assertEqual(len(commands), 1)
        self.assertIn("--unsigned", commands[0])

    def test_architecture_filter_removes_only_non_target_libraries(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            apk = Path(directory) / "patched.apk"
            with zipfile.ZipFile(apk, "w") as archive:
                archive.writestr("AndroidManifest.xml", b"manifest")
                archive.writestr("classes.dex", b"dex")
                archive.writestr("lib/arm64-v8a/libapp.so", b"arm64")
                archive.writestr("lib/armeabi-v7a/libapp.so", b"arm32")
                archive.writestr("lib/mips/libapp.so", b"mips")
                archive.writestr("assets/keep.txt", b"keep")

            build_main._strip_libs(apk, "arm64-v8a")

            with zipfile.ZipFile(apk) as archive:
                self.assertEqual(
                    set(archive.namelist()),
                    {
                        "AndroidManifest.xml",
                        "classes.dex",
                        "lib/arm64-v8a/libapp.so",
                        "assets/keep.txt",
                    },
                )
                self.assertEqual(archive.read("assets/keep.txt"), b"keep")

    def test_architecture_filter_normalizes_even_without_library_removal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            apk = Path(directory) / "patched.apk"
            with zipfile.ZipFile(apk, "w") as archive:
                archive.writestr("AndroidManifest.xml", b"manifest")
                archive.writestr("lib/arm64-v8a/libapp.so", b"arm64")
            malformed = bytearray(apk.read_bytes())
            local_flags = struct.unpack_from("<H", malformed, 6)[0]
            struct.pack_into("<H", malformed, 6, local_flags | 0x08)
            apk.write_bytes(malformed)

            build_main._strip_libs(apk, "arm64-v8a")

            self.assertTrue(zipfile.is_zipfile(apk))
            normalized = apk.read_bytes()
            central_offset = normalized.index(b"PK\x01\x02")
            self.assertEqual(
                struct.unpack_from("<H", normalized, 6)[0] & 0x08,
                struct.unpack_from("<H", normalized, central_offset + 8)[0] & 0x08,
            )
            with zipfile.ZipFile(apk) as archive:
                self.assertEqual(archive.read("lib/arm64-v8a/libapp.so"), b"arm64")

    def test_yuucho_uses_upstreams_universal_patch_repository(self) -> None:
        config = json.loads((ROOT / "my-patch-config.json").read_text(encoding="utf-8"))
        entries = {
            item["app_name"]: item
            for item in config["patch_list"]
            if item["app_name"] in {"yuucho-tsucho", "yuucho-ninsho"}
        }
        self.assertEqual(set(entries), {"yuucho-tsucho", "yuucho-ninsho"})
        for item in entries.values():
            self.assertEqual(item["source"], "rushiranpise-universal")
            self.assertEqual(item["force_enable"], ["Hide ADB status"])
            self.assertEqual(item["required"], ["Hide ADB status"])

        source = json.loads(
            (ROOT / "sources/rushiranpise-universal.json").read_text(encoding="utf-8")
        )
        self.assertEqual(source[2]["user"], "rushiranpise")
        self.assertEqual(source[2]["repo"], "Ri-Vanced-Universal-Morphe-Patches")
        self.assertEqual(
            download_all_tools.PATCHES_LIST_FILES["rushiranpise-universal"],
            "patches-list.json",
        )

    def test_apksigner_can_be_found_from_configured_android_sdk(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            signer = Path(directory) / "build-tools/36.0.0/apksigner.bat"
            signer.parent.mkdir(parents=True)
            signer.write_text("", encoding="utf-8")
            with (
                mock.patch.dict(
                    os.environ,
                    {"ANDROID_HOME": directory, "ANDROID_SDK_ROOT": ""},
                ),
                mock.patch.object(utils.shutil, "which", return_value=None),
            ):
                self.assertEqual(utils.find_apksigner(), str(signer))


if __name__ == "__main__":
    unittest.main()
