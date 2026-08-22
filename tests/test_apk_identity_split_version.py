import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from src.apk_identity import ApkIdentity, read_identity


class SplitBundleIdentityTests(unittest.TestCase):
    @mock.patch("src.apk_identity._read_plain_apk_identity")
    @mock.patch("src.apk_identity.find_aapt", return_value="aapt")
    def test_prefers_split_identity_with_version_name(
        self,
        _find_aapt: mock.Mock,
        read_plain: mock.Mock,
    ) -> None:
        read_plain.side_effect = [
            ApkIdentity("com.example.app", "", "123"),
            ApkIdentity("com.example.app", "1.2.3", "123"),
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "google-play.apks"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("config.arm64_v8a.apk", b"config")
                archive.writestr("split_with_version.apk", b"versioned")

            identity = read_identity(path)

        self.assertEqual(identity.version_name, "1.2.3")
        self.assertEqual(identity.version_code, "123")
        self.assertEqual(read_plain.call_count, 2)


if __name__ == "__main__":
    unittest.main()
