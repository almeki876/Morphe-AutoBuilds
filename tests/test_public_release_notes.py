import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import release_notes


class PublicReleaseNotesTests(unittest.TestCase):
    def test_only_successful_apk_artifacts_are_publicly_listed(self) -> None:
        matrix = [
            {"app_name": "amazon-shopping", "source": "rushiranpise"},
            {"app_name": "yuucho-tsucho", "source": "rushiranpise"},
        ]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            successful = root / "apk-amazon-shopping-rushiranpise"
            successful.mkdir()
            (successful / "Amazon.apk").write_bytes(b"apk")
            with mock.patch.dict(
                os.environ,
                {
                    "EXPECTED_MATRIX": json.dumps(matrix),
                    "ARTIFACT_ROOT": str(root),
                },
            ):
                selected = release_notes._successful_release_matrix()

        self.assertEqual(selected, [matrix[0]])

    def test_release_notes_are_minimal_and_link_to_patch_source(self) -> None:
        selected = [
            {"app_name": "youtube", "source": "revanced-anddea"},
            {"app_name": "youtube-music", "source": "revanced-anddea"},
        ]
        source_url = "https://github.com/anddea/revanced-patches"
        with (
            mock.patch.object(
                release_notes, "_successful_release_matrix", return_value=selected
            ),
            mock.patch.object(release_notes, "_load_event_inputs", return_value={}),
            mock.patch.object(release_notes, "_source_url", return_value=source_url),
            mock.patch.dict(
                os.environ, {"GITHUB_REPOSITORY": "example/Morphe-AutoBuilds"}
            ),
        ):
            published = release_notes.render()

        self.assertIn("| Apps | Patch source |", published)
        self.assertIn("YouTube, YouTube Music", published)
        self.assertIn(f"[Anddea]({source_url})", published)
        self.assertIn(
            "[Details / 詳細](https://github.com/example/Morphe-AutoBuilds#readme)",
            published,
        )
        self.assertNotIn("| Version |", published)
        self.assertNotIn("| Status |", published)
        self.assertNotIn("JST", published)

    def test_public_release_omits_internal_and_clean_scan_reports(self) -> None:
        original_directory = Path.cwd()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "build_status.md").write_text(
                "FAILED JOB: internal traceback", encoding="utf-8"
            )
            (root / "virustotal_base_results.md").write_text(
                "Security scan: clean", encoding="utf-8"
            )
            try:
                os.chdir(root)
                with mock.patch.object(
                    release_notes, "render", return_value="# User release notes\n"
                ):
                    release_notes.main()
                published = (root / "release_notes.md").read_text(encoding="utf-8")
            finally:
                os.chdir(original_directory)

        self.assertNotIn("FAILED JOB", published)
        self.assertNotIn("internal traceback", published)
        self.assertNotIn("Security scan: clean", published)

    def test_release_tag_prefers_explicit_release_tag(self) -> None:
        with mock.patch.dict(
            os.environ,
            {"RELEASE_TAG": "2026-08-24_14-30-JST"},
            clear=False,
        ):
            self.assertEqual(
                release_notes._release_tag_from_previous_step(),
                "2026-08-24_14-30-JST",
            )

    def test_main_uses_prepublication_details_path_when_available(self) -> None:
        with (
            mock.patch.object(
                release_notes,
                "_generate_and_publish_release_details",
                return_value=True,
            ) as generate,
            mock.patch.object(release_notes, "render") as legacy_render,
        ):
            release_notes.main()

        generate.assert_called_once_with(Path("release_notes.md"))
        legacy_render.assert_not_called()


if __name__ == "__main__":
    unittest.main()
