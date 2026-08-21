import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import download_apks
from src.apk_identity import ApkIdentity, ApkIdentityError
from src.versioning import VersionCandidate


class DownloadApksTests(unittest.TestCase):
    @mock.patch("scripts.download_apks._cache_snapshot", return_value=set())
    @mock.patch("scripts.download_apks._new_cache_entries", return_value=set())
    @mock.patch("scripts.download_apks.providers.load_config", return_value={})
    @mock.patch("scripts.download_apks.providers.download_priority", return_value=["first", "second"])
    @mock.patch("scripts.download_apks.providers.configured_package", return_value="com.example.app")
    @mock.patch("scripts.download_apks.utils.get_supported_version_candidates")
    @mock.patch("scripts.download_apks._find_tools")
    @mock.patch("scripts.download_apks.downloader.remove_apk_origin")
    @mock.patch("scripts.download_apks.downloader.download_platform")
    @mock.patch("scripts.download_apks.apk_identity.validate_identity")
    def test_version_mismatch_tries_next_provider(
        self,
        validate_identity: mock.Mock,
        download_platform: mock.Mock,
        remove_origin: mock.Mock,
        find_tools: mock.Mock,
        supported_versions: mock.Mock,
        configured_package: mock.Mock,
        download_priority: mock.Mock,
        load_config: mock.Mock,
        new_cache_entries: mock.Mock,
        cache_snapshot: mock.Mock,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            wrong = root / "wrong.apk"
            correct = root / "correct.apk"
            wrong.write_bytes(b"wrong")
            correct.write_bytes(b"correct")

            find_tools.return_value = ([], Path("cli.jar"), Path("patches.mpp"))
            supported_versions.return_value = [VersionCandidate(name="1.2.3")]
            download_platform.side_effect = [
                (wrong, "1.2.3"),
                (correct, "1.2.3"),
            ]
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
        self.assertEqual(download_platform.call_count, 2)
        remove_origin.assert_called_once_with("example", "arm64-v8a")

    @mock.patch("src.provenance.record")
    @mock.patch("scripts.download_apks.apk_cache.stage")
    @mock.patch("scripts.download_apks.apk_cache.is_valid_apk_archive", return_value=True)
    @mock.patch("scripts.download_apks.providers.download_priority", return_value=[])
    @mock.patch("scripts.download_apks.providers.configured_package", return_value="com.example.app")
    @mock.patch("scripts.download_apks.utils.get_supported_version_candidates")
    @mock.patch("scripts.download_apks._find_tools")
    @mock.patch("scripts.download_apks.downloader.download_with_apkeep")
    @mock.patch("scripts.download_apks.downloader.download_with_justapk")
    @mock.patch("scripts.download_apks.apk_identity.validate_identity")
    def test_fallback_identity_mismatch_tries_next_fallback(
        self,
        validate_identity: mock.Mock,
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
        stage.assert_called_once_with(correct, "com.example.app", "1.2.3", "apkeep")
        record.assert_called_once()

    @mock.patch("src.provenance.record")
    @mock.patch("scripts.download_apks.apk_cache.stage")
    @mock.patch("scripts.download_apks.apk_cache.is_valid_apk_archive", return_value=True)
    @mock.patch("scripts.download_apks.providers.load_config")
    @mock.patch("scripts.download_apks.providers.download_priority", return_value=["apkpure"])
    @mock.patch("scripts.download_apks.providers.configured_package", return_value="com.example.app")
    @mock.patch("scripts.download_apks.utils.get_supported_version_candidates")
    @mock.patch("scripts.download_apks._find_tools")
    @mock.patch("scripts.download_apks.downloader.download_platform", return_value=(None, None))
    @mock.patch("scripts.download_apks.downloader.download_with_apkeep")
    @mock.patch("scripts.download_apks.aurora_play.download_current")
    @mock.patch("scripts.download_apks.downloader.download_with_justapk")
    @mock.patch("scripts.download_apks.apk_identity.validate_identity")
    def test_opt_in_uses_aurora_after_mislabeled_justapk(
        self,
        validate_identity: mock.Mock,
        download_with_justapk: mock.Mock,
        aurora_download: mock.Mock,
        download_with_apkeep: mock.Mock,
        download_platform: mock.Mock,
        find_tools: mock.Mock,
        supported_versions: mock.Mock,
        configured_package: mock.Mock,
        download_priority: mock.Mock,
        load_config: mock.Mock,
        is_valid_apk_archive: mock.Mock,
        stage: mock.Mock,
        record: mock.Mock,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            wrong = root / "wrong.apk"
            play = root / "play.apk"
            wrong.write_bytes(b"wrong")
            play.write_bytes(b"play")

            find_tools.return_value = ([], Path("cli.jar"), Path("patches.mpp"))
            supported_versions.return_value = [VersionCandidate(name="32.13.2.100")]
            load_config.return_value = {"google_play_fallback": True}
            download_with_justapk.return_value = wrong
            aurora_download.return_value = play
            validate_identity.side_effect = [
                ApkIdentityError(
                    "APK version mismatch: expected 32.13.2.100, actual 32.13.0.100"
                ),
                ApkIdentity("com.example.app", "32.13.2.100", "1241322016"),
            ]

            path, version = download_apks._download("example", "source", "universal")

        self.assertEqual(path, play)
        self.assertEqual(version, "32.13.2.100")
        self.assertFalse(wrong.exists())
        aurora_download.assert_called_once_with("com.example.app", Path("."))
        download_with_apkeep.assert_not_called()
        stage.assert_called_once_with(
            play,
            "com.example.app",
            "32.13.2.100",
            "aurora-google-play",
        )
        record.assert_called_once()


if __name__ == "__main__":
    unittest.main()
