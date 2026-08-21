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


if __name__ == "__main__":
    unittest.main()
