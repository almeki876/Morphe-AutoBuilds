import unittest
from pathlib import Path
from unittest import mock

from src.apk_identity import (
    ApkIdentity,
    ApkIdentityError,
    parse_badging,
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

    @mock.patch("src.apk_identity.read_identity")
    def test_accepts_matching_package_and_version(self, read_identity: mock.Mock) -> None:
        read_identity.return_value = ApkIdentity("com.example.app", "1.2.3", "123")
        result = validate_identity(
            Path("app.apk"),
            "com.example.app",
            VersionCandidate(name="1.2.3", code="123"),
        )
        self.assertEqual(result.version_name, "1.2.3")

    @mock.patch("src.apk_identity.read_identity")
    def test_rejects_package_mismatch(self, read_identity: mock.Mock) -> None:
        read_identity.return_value = ApkIdentity("com.other.app", "1.2.3", "123")
        with self.assertRaisesRegex(ApkIdentityError, "package mismatch"):
            validate_identity(Path("app.apk"), "com.example.app")

    @mock.patch("src.apk_identity.read_identity")
    def test_rejects_version_name_mismatch(self, read_identity: mock.Mock) -> None:
        read_identity.return_value = ApkIdentity("com.example.app", "9.9.9", "999")
        with self.assertRaisesRegex(ApkIdentityError, "version mismatch"):
            validate_identity(
                Path("app.apk"),
                "com.example.app",
                VersionCandidate(name="1.2.3"),
            )

    @mock.patch("src.apk_identity.read_identity")
    def test_rejects_version_code_mismatch_when_known(self, read_identity: mock.Mock) -> None:
        read_identity.return_value = ApkIdentity("com.example.app", "1.2.3", "999")
        with self.assertRaisesRegex(ApkIdentityError, "version mismatch"):
            validate_identity(
                Path("app.apk"),
                "com.example.app",
                VersionCandidate(name="1.2.3", code="123"),
            )


if __name__ == "__main__":
    unittest.main()
