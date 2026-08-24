import argparse
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import generate_release_details as details


class ReleaseDetailsTests(unittest.TestCase):
    def test_vt_results_are_scoped_to_app_and_patch_source(self) -> None:
        payload = {
            "results": [
                {"file": "gboard-jason-arm64-v8a.apk", "verdict": "clean"},
                {"file": "youtube-morphe-arm64-v8a.apk", "verdict": "clean"},
            ]
        }
        self.assertEqual(
            details._find_vt(payload, "gboard", "jason"),
            [payload["results"][0]],
        )

    def test_gboard_applied_patch_is_attributed_to_supplemental_source(self) -> None:
        config = [
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
        effective = details._gboard_supplemental_selections(config)
        self.assertEqual(effective["adobo"], {"Enable Undo feature"})
        self.assertEqual(
            effective["morning-entree"],
            {"Always incognito mode", "Block tracking and analytics"},
        )
        self.assertEqual(
            details._patch_source_for(
                "gboard", "jason", "Enable Undo feature", effective
            ),
            "adobo",
        )
        self.assertEqual(
            details._patch_source_for(
                "gboard", "jason", "Always incognito mode", effective
            ),
            "morning-entree",
        )
        self.assertEqual(
            details._patch_source_for(
                "gboard", "jason", "Some Jason patch", effective
            ),
            "jason",
        )

    def test_generate_creates_release_and_app_indexes_from_actual_reports(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            build = root / "build"
            download = root / "download"
            base = root / "base" / "base-input-gboard-jason" / "base-apk-input"
            build.mkdir(parents=True)
            download.mkdir(parents=True)
            base.mkdir(parents=True)

            report = {
                "app_name": "gboard",
                "source": "jason",
                "source_name": "jasonwu1994",
                "version": "18.0.3",
                "status": "success",
                "lifecycle_status": "success_full",
                "applying_count": 3,
                "applied_patches": [
                    "Some Jason patch",
                    "Enable Undo feature",
                    "Always incognito mode",
                ],
                "required_patches_satisfied": True,
            }
            (build / "gboard-jason.json").write_text(
                json.dumps(report), encoding="utf-8"
            )
            (build / "gboard-jason-build.txt").write_text(
                "status=success\n", encoding="utf-8"
            )
            (download / "gboard-jason-download.txt").write_text(
                "status=success\n", encoding="utf-8"
            )
            origin = {
                "app_name": "gboard",
                "patch_source": "jason",
                "version": "18.0.3",
                "architecture": "arm64-v8a",
                "provider": "apkmirror",
                "provider_label": "APKMirror",
                "provider_url": "https://www.apkmirror.com/",
                "origin_url": "https://www.apkmirror.com/example/",
                "cached": True,
                "cache_tag": "base-apk-cache-v2",
            }
            (base / "origin.json").write_text(json.dumps(origin), encoding="utf-8")
            vt_path = root / "vt.json"
            vt_path.write_text(
                json.dumps(
                    {
                        "results": [
                            {
                                "file": "gboard-jason-arm64-v8a.apk",
                                "sha256": "abc",
                                "malicious": 0,
                                "suspicious": 0,
                                "verdict": "clean",
                                "method": "hash lookup",
                                "permalink": "https://www.virustotal.com/gui/file/abc",
                                "engines": {},
                            }
                        ],
                        "failures": [],
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
                                "force_enable": ["Enable Undo feature"],
                            },
                            {
                                "app_name": "gboard",
                                "source": "morning-entree",
                                "force_enable": ["Always incognito mode"],
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )
            (root / "last-tags.json").write_text("{}", encoding="utf-8")

            args = argparse.Namespace(
                tag="2026-08-24_13-30-JST",
                repository="example/repo",
                run_url="https://github.com/example/repo/actions/runs/1",
                build_results=build,
                download_results=download,
                base_inputs=root / "base",
                virustotal=vt_path,
                output_root=root / "release-details",
                release_notes=root / "release-notes-details.md",
            )
            cwd = Path.cwd()
            try:
                os.chdir(root)
                with (
                    mock.patch.object(details, "_source_url", return_value="https://example.invalid/source"),
                    mock.patch.object(details, "_patch_entries", return_value={}),
                ):
                    release_dir, notes_path = details.generate(args)
            finally:
                os.chdir(cwd)

            release_index = (release_dir / "README.md").read_text(encoding="utf-8")
            app_index = (release_dir / "apps/gboard/README.md").read_text(encoding="utf-8")
            patches = (release_dir / "apps/gboard/patches.md").read_text(encoding="utf-8")
            apk_source = (release_dir / "apps/gboard/apk-source.md").read_text(encoding="utf-8")
            vt = (release_dir / "apps/gboard/virustotal.md").read_text(encoding="utf-8")
            notes = notes_path.read_text(encoding="utf-8")

            self.assertIn("収録アプリ数:** 1", release_index)
            self.assertIn("Jason + Adobo + Morning-Entree", release_index)
            self.assertIn("開発者向け診断・ログ", app_index)
            self.assertIn("Enable Undo feature", patches)
            self.assertIn("jkennethcarino", patches)
            self.assertIn("Always incognito mode", patches)
            self.assertIn("Entree3k", patches)
            self.assertIn("GitHub Base APK Cache から復元", apk_source)
            self.assertIn("APKMirror", apk_source)
            self.assertIn("virustotal.com/gui/file/abc", vt)
            self.assertIn("[Gboard](https://github.com/example/repo/blob/main/", notes)


if __name__ == "__main__":
    unittest.main()
