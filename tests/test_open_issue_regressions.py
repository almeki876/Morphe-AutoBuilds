import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src import aurora_play
from src.versioning import VersionCandidate, parse_candidate


class OpenIssueRegressionTests(unittest.TestCase):
    def test_numeric_cli_version_is_treated_as_version_code(self) -> None:
        candidate = parse_candidate("88600 (1 patch)")
        self.assertIsNotNone(candidate)
        assert candidate is not None
        self.assertEqual(candidate.code, "88600")
        self.assertTrue(candidate.matches("8.8.6", "88600"))
        self.assertFalse(candidate.matches("8.8.9", "88900"))

    def test_poweramp_build_version_exposes_google_play_version_code(self) -> None:
        candidate = parse_candidate("build-1025-bundle-play (1 patch)")
        self.assertIsNotNone(candidate)
        assert candidate is not None
        self.assertEqual(candidate.name, "build-1025-bundle-play")
        self.assertEqual(candidate.code, "1025")

    def test_composite_asset_version_matches_manifest_name_and_code(self) -> None:
        candidate = VersionCandidate(name="21.0.0.40")
        self.assertTrue(candidate.matches("21.0.0", "40"))
        self.assertFalse(candidate.matches("21.0.0", "41"))
        self.assertFalse(candidate.matches("20.0.0", "40"))

    def test_stale_cached_gplaydl_jar_is_rebuilt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "pom.xml"
            source_root = root / "src"
            source = source_root / "main" / "kotlin" / "Main.kt"
            jar = root / "target" / "gplaydl-1.0-SNAPSHOT-all.jar"
            fingerprint_file = root / "target" / "gplaydl-source.sha256"
            source.parent.mkdir(parents=True)
            jar.parent.mkdir(parents=True)
            project.write_text("<project>new</project>", encoding="utf-8")
            source.write_text("fun main() = println(\"new\")", encoding="utf-8")
            jar.write_bytes(b"old-jar")
            fingerprint_file.write_text("stale\n", encoding="utf-8")

            def fake_run(command, *, cwd=None, env=None):
                jar.write_bytes(b"new-jar")
                return mock.Mock(returncode=0, stdout="ok")

            with (
                mock.patch.object(aurora_play, "GPLAYDL_PROJECT", project),
                mock.patch.object(aurora_play, "GPLAYDL_SOURCE_ROOT", source_root),
                mock.patch.object(aurora_play, "GPLAYDL_JAR", jar),
                mock.patch.object(aurora_play, "GPLAYDL_FINGERPRINT", fingerprint_file),
                mock.patch("src.aurora_play.shutil.which", return_value="/usr/bin/mvn"),
                mock.patch("src.aurora_play._run", side_effect=fake_run) as run,
            ):
                result = aurora_play._ensure_downloader()

            self.assertEqual(result, jar)
            self.assertEqual(jar.read_bytes(), b"new-jar")
            self.assertNotEqual(fingerprint_file.read_text(encoding="utf-8").strip(), "stale")
            run.assert_called_once()


if __name__ == "__main__":
    unittest.main()
