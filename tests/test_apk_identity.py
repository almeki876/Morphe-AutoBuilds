import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from src.apk_identity import (
    ApkIdentity,
    ApkIdentityError,
    parse_badging,
    read_identity,
    validate_identity,
)
from src.versioning import VersionCandidate


class ApkIdentityTests(unittest.TestCase):
    def test_parse_badging(self) -> None:
        identity = parse_badging(
            "package: name='com.example.app' versionCode='123' versionName='1.2.3' platformBuildVersionName=''\n"
        )
        self.assertEqual(
            identity,
            ApkIdentity("com.example.app", "1.2.3", "123"),
        )

    @mock.patch("src.apk_identity._read_plain_apk_identity")
    @mock.patch("src.apk_identity.find_aapt", return_value="aapt")
    def test_reads_suffixless_plain_apk_before_treating_it_as_container(
        self,
        find_aapt: mock.Mock,
        read_plain: mock.Mock,
    ) -> None:
        expected = ApkIdentity("com.example.app", "1.2.3", "123")
        read_plain.return_value = expected
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "provider-download-without-extension"
            path.write_bytes(b"placeholder")
            self.assertEqual(read_identity(path), expected)
        self.assertEqual(read_plain.call_args.args[0], path)
        find_aapt.assert_called_once_with()

    @mock.patch("src.apk_identity._read_plain_apk_identity")
    @mock.patch("src.apk_identity.find_aapt", return_value="aapt")
    def test_reads_base_apk_first_from_split_container(
        self,
        find_aapt: mock.Mock,
        read_plain: mock.Mock,
    ) -> None:
        expected = ApkIdentity("com.example.app", "1.2.3", "123")
        read_plain.side_effect = [
            ApkIdentityError("container is not a plain APK"),
            expected,
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bundle.apkm"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("split_config.en.apk", b"language")
                archive.writestr("base.apk", b"base")
            self.assertEqual(read_identity(path), expected)
        self.assertEqual(read_plain.call_count, 2)
        first_path = read_plain.call_args_list[1].args[0]
        self.assertEqual(first_path.name, "candidate-0.apk")
        find_aapt.assert_called_once_with()

    @mock.patch("src.apk_identity.read_identity")
    def test_accepts_matching_package_and_version(self, read_identity_mock: mock.Mock) -> None:
        read_identity_mock.return_value = ApkIdentity("com.example.app", "1.2.3", "123")
        result = validate_identity(
            Path("app.apk"),
            "com.example.app",
            VersionCandidate(name="1.2.3", code="123"),
        )
        self.assertEqual(result.version_name, "1.2.3")

    @mock.patch("src.apk_identity.read_identity")
    def test_accepts_vendor_combined_version_name_and_code(
        self,
        read_identity_mock: mock.Mock,
    ) -> None:
        read_identity_mock.return_value = ApkIdentity("com.example.app", "21.0.0", "40")
        result = validate_identity(
            Path("app.apk"),
            "com.example.app",
            VersionCandidate(name="21.0.0.40"),
        )
        self.assertEqual(result.version_code, "40")

    @mock.patch("src.apk_identity.read_identity")
    def test_rejects_package_mismatch(self, read_identity_mock: mock.Mock) -> None:
        read_identity_mock.return_value = ApkIdentity("com.other.app", "1.2.3", "123")
        with self.assertRaisesRegex(ApkIdentityError, "package mismatch"):
            validate_identity(Path("app.apk"), "com.example.app")

    @mock.patch("src.apk_identity.read_identity")
    def test_rejects_version_name_mismatch(self, read_identity_mock: mock.Mock) -> None:
        read_identity_mock.return_value = ApkIdentity("com.example.app", "9.9.9", "999")
        with self.assertRaisesRegex(ApkIdentityError, "version mismatch"):
            validate_identity(
                Path("app.apk"),
                "com.example.app",
                VersionCandidate(name="1.2.3"),
            )

    @mock.patch("src.apk_identity.read_identity")
    def test_rejects_version_code_mismatch_when_known(self, read_identity_mock: mock.Mock) -> None:
        read_identity_mock.return_value = ApkIdentity("com.example.app", "1.2.3", "999")
        with self.assertRaisesRegex(ApkIdentityError, "version mismatch"):
            validate_identity(
                Path("app.apk"),
                "com.example.app",
                VersionCandidate(name="1.2.3", code="123"),
            )


if __name__ == "__main__":
    unittest.main()
