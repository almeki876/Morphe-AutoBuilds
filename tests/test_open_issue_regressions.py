import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import download_apks
from src import apk_identity, aurora_play, uptodown_direct, versioning
from src.versioning import VersionCandidate, parse_candidate


class OpenIssueRegressionTests(unittest.TestCase):
    def test_numeric_cli_version_is_treated_as_version_code(self) -> None:
        candidate = parse_candidate("88600 (1 patch)")
        self.assertIsNotNone(candidate)
        assert candidate is not None
        self.assertEqual(candidate.code, "88600")
        self.assertTrue(candidate.matches("8.8.6", "88600"))
        self.assertFalse(candidate.matches("8.8.9", "88900"))

    def test_nova_code_prefixed_manifest_name_matches_exact_candidate(self) -> None:
        candidate = parse_candidate("88600 (8.8.6)")
        self.assertIsNotNone(candidate)
        assert candidate is not None
        self.assertTrue(candidate.matches("88600 (8.8.6)", "88600"))
        self.assertFalse(candidate.matches("88600 (8.8.5)", "88600"))
        self.assertFalse(candidate.matches("88600 (8.8.6)", "88500"))

    def test_exact_version_code_allows_missing_split_version_name(self) -> None:
        candidate = VersionCandidate(name="12.12.0", code="121200004")
        self.assertTrue(candidate.matches("", "121200004"))
        self.assertFalse(candidate.matches("", "121200003"))
        self.assertFalse(VersionCandidate(name="12.12.0").matches("", "121200004"))

    def test_aapt_package_line_survives_later_resource_error(self) -> None:
        completed = mock.Mock(
            returncode=1,
            stdout="package: name='tv.twitch.android.app' versionCode='1100000018' versionName='13.0.0.2'\n",
            stderr="AndroidManifest.xml:26: error: ERROR getting 'android:icon' attribute",
        )
        with mock.patch("src.apk_identity.subprocess.run", return_value=completed):
            identity = apk_identity._read_plain_apk_identity(Path("twitch.apk"), "aapt")
        self.assertEqual(identity.package_name, "tv.twitch.android.app")
        self.assertEqual(identity.version_name, "13.0.0.2")
        self.assertEqual(identity.version_code, "1100000018")

    def test_discovered_provider_code_enriches_expected_identity(self) -> None:
        package = "com.overlook.android.fing"
        versioning.remember_version_code(package, "12.12.0", "121200004")
        with (
            mock.patch("scripts.download_apks.providers.load_config", return_value={}),
            mock.patch("scripts.download_apks.providers.configured_package", return_value=package),
        ):
            candidate = download_apks._expected_candidate(
                "fing", "apkmirror", "12.12.0", [VersionCandidate(name="12.12.0")]
            )
        self.assertEqual(candidate.code, "121200004")
        self.assertTrue(candidate.matches("", "121200004"))

    def test_poweramp_build_label_is_not_android_version_code(self) -> None:
        candidate = parse_candidate("build-1025-bundle-play (1 patch)")
        self.assertIsNotNone(candidate)
        assert candidate is not None
        self.assertEqual(candidate.name, "build-1025-bundle-play")
        self.assertIsNone(candidate.code)
        self.assertTrue(candidate.matches("build-1025-bundle-play", "1025004"))
        self.assertFalse(candidate.matches("build-1024-bundle-play", "1025004"))

    def test_composite_asset_version_matches_manifest_name_and_code(self) -> None:
        candidate = VersionCandidate(name="21.0.0.40")
        self.assertTrue(candidate.matches("21.0.0", "40"))
        self.assertFalse(candidate.matches("21.0.0", "41"))
        self.assertFalse(candidate.matches("20.0.0", "40"))

    @mock.patch("scripts.download_apks.providers.download_priority", return_value=["apkpure"])
    @mock.patch(
        "scripts.download_apks.providers.load_config",
        return_value={"version": "32.13.2.100", "version_code": "1241320216"},
    )
    def test_patch_version_is_enriched_with_configured_version_code(
        self,
        load_config: mock.Mock,
        download_priority: mock.Mock,
    ) -> None:
        candidate = VersionCandidate(name="32.13.2.100")
        selected = download_apks._preferred_play_candidate(
            "amazon-shopping",
            "com.amazon.mShop.android.shopping",
            [candidate],
        )
        self.assertIsNotNone(selected)
        assert selected is not None
        self.assertEqual(selected.name, "32.13.2.100")
        self.assertEqual(selected.code, "1241320216")

    def test_uptodown_history_card_uses_current_data_attributes(self) -> None:
        soup = uptodown_direct.BeautifulSoup(
            """
            <div data-version-id="987654"
                 data-url="https://amazon-shopping.en.uptodown.com/android"
                 data-extra-url="download">
              <span class="type">xapk</span>
              <span class="version">32.13.2.100</span>
            </div>
            """,
            "html.parser",
        )
        card = soup.select_one("[data-version-id]")
        self.assertIsNotNone(card)
        assert card is not None
        candidate = VersionCandidate(name="32.13.2.100", code="1241320216")

        self.assertTrue(
            uptodown_direct._history_card_matches_candidate(card, candidate)
        )
        self.assertEqual(
            uptodown_direct._history_download_page(
                card, "https://amazon-shopping.en.uptodown.com/android"
            ),
            "https://amazon-shopping.en.uptodown.com/android/download/987654",
        )

    def test_uptodown_history_resolves_exact_version_card_before_legacy_api(self) -> None:
        html = b"""
        <div id="versions-items-list">
          <div data-version-id="424242"
               data-url="https://crunchyroll.en.uptodown.com/android"
               data-extra-url="download">
            <span class="type">xapk</span>
            <span class="version">3.112.2</span>
          </div>
        </div>
        """
        response = mock.Mock(status_code=200, content=html)
        with (
            mock.patch.object(
                uptodown_direct,
                "_base_urls",
                return_value=["https://crunchyroll.en.uptodown.com/android"],
            ),
            mock.patch("src.uptodown_direct.utils.cf_aware_get", return_value=response),
            mock.patch(
                "src.uptodown_direct.legacy._download_url_from_page",
                return_value="https://dw.uptodown.com/dwn/example",
            ) as resolve,
        ):
            link = uptodown_direct._direct_link_from_history(
                VersionCandidate(name="3.112.2"),
                "crunchyroll",
                {"name": "crunchyroll", "package": "com.crunchyroll.crunchyroid"},
            )

        self.assertEqual(link, "https://dw.uptodown.com/dwn/example")
        resolve.assert_called_once_with(
            "https://crunchyroll.en.uptodown.com/android/download/424242"
        )

    def test_uptodown_history_rejects_external_data_url(self) -> None:
        soup = uptodown_direct.BeautifulSoup(
            """
            <div data-version-id="7"
                 data-url="https://example.invalid/android"
                 data-extra-url="download">
              <span class="version">11.4.5</span>
            </div>
            """,
            "html.parser",
        )
        card = soup.select_one("[data-version-id]")
        self.assertIsNotNone(card)
        assert card is not None
        self.assertIsNone(
            uptodown_direct._history_download_page(
                card, "https://adobe-lightroom-mobile.en.uptodown.com/android"
            )
        )

    def test_actions_restore_cannot_replace_tracked_gplaydl_sources(self) -> None:
        completed = mock.Mock(returncode=0, stdout="")
        with (
            mock.patch.dict("os.environ", {"GITHUB_ACTIONS": "true"}),
            mock.patch("src.aurora_play._run", return_value=completed) as run,
        ):
            aurora_play._restore_checked_in_gplaydl_sources()

        run.assert_called_once_with(
            [
                "git",
                "restore",
                "--source=HEAD",
                "--worktree",
                "--",
                str(aurora_play.GPLAYDL_PROJECT),
                str(aurora_play.GPLAYDL_SOURCE_ROOT),
            ]
        )

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
                mock.patch.dict("os.environ", {"GITHUB_ACTIONS": "false"}),
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
