import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import download_apks
from src.apk_identity import ApkIdentity, ApkIdentityError
from src.versioning import VersionCandidate


class DownloadApksTests(unittest.TestCase):
    def setUp(self) -> None:
        resolver = mock.patch(
            "scripts.download_apks.providers.resolve_patch_candidates",
            side_effect=lambda app_name, package, candidates: candidates,
        )
        resolver.start()
        self.addCleanup(resolver.stop)

    def test_explicit_patch_candidate_is_primary_play_release(self) -> None:
        newest = VersionCandidate(name="9.9.9", code="999")
        older = VersionCandidate(name="9.8.0", code="980")
        self.assertIs(
            download_apks._preferred_play_candidate(
                "example", "com.example.app", [newest, older]
            ),
            newest,
        )

    def test_any_patch_compatibility_requests_current_play_release(self) -> None:
        self.assertIsNone(
            download_apks._preferred_play_candidate("example", "com.example.app", [])
        )

    @mock.patch("scripts.download_apks._cache_snapshot", return_value=set())
    @mock.patch("scripts.download_apks.providers.load_config", return_value={})
    @mock.patch("scripts.download_apks.providers.download_priority", return_value=["github"])
    @mock.patch("scripts.download_apks.providers.configured_package", return_value="com.adguard.android")
    @mock.patch("scripts.download_apks.utils.get_supported_version_candidates", return_value=[])
    @mock.patch("scripts.download_apks._find_tools", return_value=([], Path("cli.jar"), Path("patches.mpp")))
    @mock.patch("scripts.download_apks.downloader.download_platform")
    @mock.patch("scripts.download_apks.aurora_play.download_candidate")
    @mock.patch("scripts.download_apks.apk_identity.validate_identity")
    def test_adguard_skips_play_and_uses_github_provider(
        self,
        validate_identity: mock.Mock,
        play_download: mock.Mock,
        download_platform: mock.Mock,
        find_tools: mock.Mock,
        supported_versions: mock.Mock,
        configured_package: mock.Mock,
        download_priority: mock.Mock,
        load_config: mock.Mock,
        cache_snapshot: mock.Mock,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            github_apk = Path(directory) / "adguard.apk"
            github_apk.write_bytes(b"apk")
            download_platform.return_value = (github_apk, "4.9.2")
            validate_identity.return_value = ApkIdentity(
                "com.adguard.android", "4.9.2", "40090200"
            )

            path, version = download_apks._download("adguard", "source", "universal")

        self.assertEqual(path, github_apk)
        self.assertEqual(version, "4.9.2")
        play_download.assert_not_called()
        download_platform.assert_called_once()

    @mock.patch("scripts.download_apks.downloader.download_with_apkeep")
    @mock.patch("scripts.download_apks.downloader.download_with_justapk")
    @mock.patch("scripts.download_apks._cache_snapshot", return_value=set())
    @mock.patch("scripts.download_apks.providers.download_priority", return_value=["github"])
    @mock.patch("scripts.download_apks.providers.configured_package", return_value="com.adguard.android")
    @mock.patch("scripts.download_apks.utils.get_supported_version_candidates", return_value=[])
    @mock.patch("scripts.download_apks._find_tools", return_value=([], Path("cli.jar"), Path("patches.mpp")))
    @mock.patch("scripts.download_apks.downloader.download_platform", return_value=(None, None))
    @mock.patch("scripts.download_apks.aurora_play.download_candidate")
    def test_adguard_refuses_mirror_fallback_when_github_fails(
        self,
        play_download: mock.Mock,
        download_platform: mock.Mock,
        find_tools: mock.Mock,
        supported_versions: mock.Mock,
        configured_package: mock.Mock,
        download_priority: mock.Mock,
        cache_snapshot: mock.Mock,
        justapk: mock.Mock,
        apkeep: mock.Mock,
    ) -> None:
        with self.assertRaisesRegex(RuntimeError, "GitHub-only provider failed"):
            download_apks._download("adguard", "source", "universal")

        play_download.assert_not_called()
        download_platform.assert_called_once()
        justapk.assert_not_called()
        apkeep.assert_not_called()

    @mock.patch("scripts.download_apks.downloader.download_with_apkeep")
    @mock.patch("scripts.download_apks.downloader.download_with_justapk")
    @mock.patch("scripts.download_apks.providers.download_priority")
    @mock.patch("scripts.download_apks.providers.google_play_only", return_value=True)
    @mock.patch("scripts.download_apks.providers.configured_package", return_value="com.example.jp")
    @mock.patch("scripts.download_apks.utils.get_supported_version_candidates", return_value=[])
    @mock.patch("scripts.download_apks._find_tools", return_value=([], Path("cli.jar"), Path("patches.mpp")))
    @mock.patch("scripts.download_apks.downloader.download_platform")
    @mock.patch(
        "scripts.download_apks.aurora_play.download_candidate",
        side_effect=RuntimeError("purchase failed"),
    )
    def test_google_play_only_failure_never_uses_non_play_sources(
        self,
        play_download: mock.Mock,
        download_platform: mock.Mock,
        find_tools: mock.Mock,
        supported_versions: mock.Mock,
        configured_package: mock.Mock,
        play_only: mock.Mock,
        download_priority: mock.Mock,
        justapk: mock.Mock,
        apkeep: mock.Mock,
    ) -> None:
        with self.assertRaisesRegex(RuntimeError, "Google Play-only source failed"):
            download_apks._download("example", "source", "arm64-v8a")

        play_download.assert_called_once_with(
            "com.example.jp", None, Path("."), require_japanese=True
        )
        download_priority.assert_not_called()
        download_platform.assert_not_called()
        justapk.assert_not_called()
        apkeep.assert_not_called()

    @mock.patch("src.provenance.record")
    @mock.patch("scripts.download_apks.apk_cache.stage")
    @mock.patch("scripts.download_apks.apk_cache.is_valid_apk_archive", return_value=True)
    @mock.patch("scripts.download_apks.providers.download_priority", return_value=["apkmirror"])
    @mock.patch("scripts.download_apks.providers.configured_package", return_value="com.example.app")
    @mock.patch("scripts.download_apks.utils.get_supported_version_candidates")
    @mock.patch("scripts.download_apks._find_tools")
    @mock.patch("scripts.download_apks.downloader.download_platform")
    @mock.patch("scripts.download_apks.aurora_play.download_candidate")
    @mock.patch("scripts.download_apks.apk_identity.validate_identity")
    def test_provider_chain_uses_provider_before_google_play(
        self,
        validate_identity: mock.Mock,
        play_download: mock.Mock,
        download_platform: mock.Mock,
        find_tools: mock.Mock,
        supported_versions: mock.Mock,
        configured_package: mock.Mock,
        download_priority: mock.Mock,
        is_valid_apk_archive: mock.Mock,
        stage: mock.Mock,
        record: mock.Mock,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            play = Path(directory) / "play.apks"
            play.write_bytes(b"play")
            candidate = VersionCandidate(name="1.2.3", code="123")
            find_tools.return_value = ([], Path("cli.jar"), Path("patches.mpp"))
            supported_versions.return_value = [candidate]
            provider_apk = Path(directory) / "provider.apk"
            provider_apk.write_bytes(b"provider")
            download_platform.return_value = (provider_apk, "1.2.3")
            validate_identity.return_value = ApkIdentity("com.example.app", "1.2.3", "123")

            path, version = download_apks._download("example", "source", "arm64-v8a")

        self.assertEqual(path, provider_apk)
        self.assertEqual(version, "1.2.3")
        download_platform.assert_called_once()
        play_download.assert_not_called()

    @mock.patch("src.provenance.record")
    @mock.patch("scripts.download_apks.apk_cache.stage")
    @mock.patch("scripts.download_apks.apk_cache.is_valid_apk_archive", return_value=True)
    @mock.patch("scripts.download_apks.providers.download_priority", return_value=[])
    @mock.patch("scripts.download_apks.providers.configured_package", return_value="com.google.android.apps.recorder")
    @mock.patch("scripts.download_apks.utils.get_supported_version_candidates", return_value=[])
    @mock.patch("scripts.download_apks._find_tools", return_value=([], Path("cli.jar"), Path("patches.mpp")))
    @mock.patch("scripts.download_apks.downloader.download_platform")
    @mock.patch("scripts.download_apks.aurora_play.download_candidate")
    @mock.patch("scripts.download_apks.apk_identity.validate_identity")
    def test_google_play_uses_version_code_when_manifest_version_name_is_empty(
        self,
        validate_identity: mock.Mock,
        play_download: mock.Mock,
        download_platform: mock.Mock,
        find_tools: mock.Mock,
        supported_versions: mock.Mock,
        configured_package: mock.Mock,
        download_priority: mock.Mock,
        is_valid_apk_archive: mock.Mock,
        stage: mock.Mock,
        record: mock.Mock,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            play = Path(directory) / "recorder.apks"
            play.write_bytes(b"play")
            play_download.return_value = play
            validate_identity.return_value = ApkIdentity(
                "com.google.android.apps.recorder", "", "42422313"
            )

            path, version = download_apks._download(
                "google-recorder", "rookie", "arm64-v8a"
            )

        self.assertEqual(path, play)
        self.assertEqual(version, "42422313")
        stage.assert_called_once_with(
            play,
            "com.google.android.apps.recorder",
            "42422313",
            "aurora-google-play",
            require_japanese=False,
        )
        download_platform.assert_not_called()
        record.assert_called_once()

    @mock.patch("src.provenance.record")
    @mock.patch("scripts.download_apks.apk_cache.stage")
    @mock.patch("scripts.download_apks._cache_snapshot", return_value=set())
    @mock.patch("scripts.download_apks._new_cache_entries", return_value=set())
    @mock.patch("scripts.download_apks.providers.load_config", return_value={})
    @mock.patch("scripts.download_apks.providers.download_priority", return_value=["first"])
    @mock.patch("scripts.download_apks.providers.configured_package", return_value="com.example.app")
    @mock.patch("scripts.download_apks.utils.get_supported_version_candidates")
    @mock.patch("scripts.download_apks._find_tools")
    @mock.patch("scripts.download_apks.downloader.remove_apk_origin")
    @mock.patch("scripts.download_apks.downloader.download_platform")
    @mock.patch("scripts.download_apks.aurora_play.download_candidate")
    @mock.patch("scripts.download_apks.apk_cache.is_valid_apk_archive", return_value=True)
    @mock.patch("scripts.download_apks.apk_identity.validate_identity")
    def test_provider_version_mismatch_tries_google_play_rescue(
        self,
        validate_identity: mock.Mock,
        is_valid_apk_archive: mock.Mock,
        play_download: mock.Mock,
        download_platform: mock.Mock,
        remove_origin: mock.Mock,
        find_tools: mock.Mock,
        supported_versions: mock.Mock,
        configured_package: mock.Mock,
        download_priority: mock.Mock,
        load_config: mock.Mock,
        new_cache_entries: mock.Mock,
        cache_snapshot: mock.Mock,
        stage: mock.Mock,
        record: mock.Mock,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            wrong = root / "wrong.apk"
            correct = root / "correct.apk"
            wrong.write_bytes(b"wrong")
            correct.write_bytes(b"correct")
            candidate = VersionCandidate(name="1.2.3")

            find_tools.return_value = ([], Path("cli.jar"), Path("patches.mpp"))
            supported_versions.return_value = [candidate]
            download_platform.return_value = (wrong, "1.2.3")
            play_download.return_value = correct
            validate_identity.side_effect = [
                ApkIdentityError(
                    "APK version mismatch: expected 1.2.3, actual 999 (9.9.9)"
                ),
                ApkIdentity("com.example.app", "1.2.3", "123"),
            ]

            path, version = download_apks._download("example", "source", "arm64-v8a")

        self.assertEqual(path, correct)
        self.assertEqual(version, "1.2.3")
        self.assertFalse(wrong.exists())
        download_platform.assert_called_once()
        remove_origin.assert_called_once_with("example", "arm64-v8a")
        play_download.assert_called_once()
        stage.assert_called_once()
        record.assert_called_once()

    @mock.patch("src.provenance.record")
    @mock.patch("scripts.download_apks.apk_cache.stage")
    @mock.patch("scripts.download_apks.apk_cache.is_valid_apk_archive", return_value=True)
    @mock.patch("scripts.download_apks.providers.download_priority", return_value=[])
    @mock.patch("scripts.download_apks.providers.configured_package", return_value="com.example.app")
    @mock.patch("scripts.download_apks.utils.get_supported_version_candidates")
    @mock.patch("scripts.download_apks._find_tools")
    @mock.patch("scripts.download_apks.downloader.download_with_apkeep")
    @mock.patch("scripts.download_apks.downloader.download_with_justapk")
    @mock.patch("scripts.download_apks.aurora_play.download_candidate")
    @mock.patch("scripts.download_apks.apk_identity.validate_identity")
    def test_google_play_failure_keeps_non_browser_fallbacks(
        self,
        validate_identity: mock.Mock,
        play_download: mock.Mock,
        download_with_justapk: mock.Mock,
        download_with_apkeep: mock.Mock,
        find_tools: mock.Mock,
        supported_versions: mock.Mock,
        configured_package: mock.Mock,
        download_priority: mock.Mock,
        is_valid_apk_archive: mock.Mock,
        stage: mock.Mock,
        record: mock.Mock,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            wrong = root / "wrong.apk"
            correct = root / "correct.apk"
            wrong.write_bytes(b"wrong")
            correct.write_bytes(b"correct")

            find_tools.return_value = ([], Path("cli.jar"), Path("patches.mpp"))
            supported_versions.return_value = [VersionCandidate(name="1.2.3")]
            play_download.side_effect = RuntimeError("anonymous auth unavailable")
            download_with_justapk.return_value = wrong
            download_with_apkeep.return_value = correct
            validate_identity.side_effect = [
                ApkIdentityError(
                    "APK version mismatch: expected 1.2.3, actual 999 (9.9.9)"
                ),
                ApkIdentity("com.example.app", "1.2.3", "123"),
            ]

            path, version = download_apks._download("example", "source", "arm64-v8a")

        self.assertEqual(path, correct)
        self.assertEqual(version, "1.2.3")
        self.assertFalse(wrong.exists())
        download_with_justapk.assert_called_once()
        download_with_apkeep.assert_called_once()
        stage.assert_called_once_with(
            correct,
            "com.example.app",
            "1.2.3",
            "apkeep",
            require_japanese=False,
        )
        record.assert_called_once()


if __name__ == "__main__":
    unittest.main()
