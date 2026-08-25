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
from src.apk_language import JapaneseResourceVerificationUnavailable
from src.versioning import VersionCandidate, parse_candidate


class ApkIdentityTests(unittest.TestCase):
    @mock.patch(
        "src.apk_identity.contains_japanese",
        side_effect=JapaneseResourceVerificationUnavailable(
            "Japanese resources could not be verified (aapt/aapt2 unavailable)"
        ),
    )
    @mock.patch("src.apk_identity.read_identity")
    def test_accepts_matching_identity_when_japanese_check_is_unavailable(
        self, read_identity_mock: mock.Mock, _ja: mock.Mock
    ) -> None:
        read_identity_mock.return_value = ApkIdentity(
            "com.example.app", "1.2.3", "123"
        )
        with self.assertLogs(level="WARNING") as logs:
            result = validate_identity(Path("app.apk"), "com.example.app")
        self.assertEqual(result.version_name, "1.2.3")
        self.assertIn("accepting unverified APK", "\n".join(logs.output))

    def test_parse_badging(self) -> None:
        identity = parse_badging(
            "package: name='com.example.app' versionCode='123' versionName='1.2.3' platformBuildVersionName=''\n"
        )
        self.assertEqual(identity, ApkIdentity("com.example.app", "1.2.3", "123"))

    @mock.patch("src.apk_identity._read_plain_apk_identity")
    @mock.patch("src.apk_identity.find_aapt", return_value="aapt")
    def test_reads_base_apk_first_from_split_container(self, find_aapt: mock.Mock, read_plain: mock.Mock) -> None:
        expected = ApkIdentity("com.example.app", "1.2.3", "123")
        read_plain.return_value = expected
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bundle.apkm"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("split_config.en.apk", b"language")
                archive.writestr("base.apk", b"base")
            self.assertEqual(read_identity(path), expected)
        first_path = read_plain.call_args_list[0].args[0]
        self.assertEqual(first_path.name, "candidate-0.apk")
        self.assertEqual(read_plain.call_count, 2)
        find_aapt.assert_called_once_with()

    @mock.patch("src.apk_identity.contains_japanese", return_value=True)
    @mock.patch("src.apk_identity.read_identity")
    def test_accepts_matching_package_and_version(self, read_identity_mock: mock.Mock, _ja: mock.Mock) -> None:
        read_identity_mock.return_value = ApkIdentity("com.example.app", "1.2.3", "123")
        result = validate_identity(Path("app.apk"), "com.example.app", VersionCandidate(name="1.2.3", code="123"))
        self.assertEqual(result.version_name, "1.2.3")

    @mock.patch("src.apk_identity.contains_japanese", return_value=True)
    @mock.patch("src.apk_identity.read_identity")
    def test_accepts_numeric_patch_cli_value_as_version_name(self, read_identity_mock: mock.Mock, _ja: mock.Mock) -> None:
        read_identity_mock.return_value = ApkIdentity("com.urbandroid.sleep", "20260616", "231112")
        candidate = parse_candidate("20260616")
        self.assertIsNotNone(candidate)
        result = validate_identity(Path("app.apk"), "com.urbandroid.sleep", candidate)
        self.assertEqual(result.version_name, "20260616")

    @mock.patch("src.apk_identity.contains_japanese", return_value=True)
    @mock.patch("src.apk_identity.read_identity")
    def test_accepts_patch_cli_display_code_when_exact_version_name_matches(self, read_identity_mock: mock.Mock, _ja: mock.Mock) -> None:
        read_identity_mock.return_value = ApkIdentity("com.google.android.apps.magazines", "5.161.0.931240252", "2022244226")
        candidate = parse_candidate("931240252 (5.161.0.931240252)")
        self.assertIsNotNone(candidate)
        result = validate_identity(Path("app.apk"), "com.google.android.apps.magazines", candidate)
        self.assertEqual(result.version_code, "2022244226")

    @mock.patch("src.apk_identity.contains_japanese", return_value=True)
    @mock.patch("src.apk_identity.read_identity")
    def test_accepts_second_observed_patch_cli_code_mismatch(self, read_identity_mock: mock.Mock, _ja: mock.Mock) -> None:
        read_identity_mock.return_value = ApkIdentity("com.intsig.camscanner", "7.22.5.2607250000", "72252")
        candidate = parse_candidate("2607250000 (7.22.5.2607250000)")
        self.assertIsNotNone(candidate)
        result = validate_identity(Path("app.apk"), "com.intsig.camscanner", candidate)
        self.assertEqual(result.version_code, "72252")

    @mock.patch("src.apk_identity.read_identity")
    def test_rejects_package_mismatch(self, read_identity_mock: mock.Mock) -> None:
        read_identity_mock.return_value = ApkIdentity("com.other.app", "1.2.3", "123")
        with self.assertRaisesRegex(ApkIdentityError, "package mismatch"):
            validate_identity(Path("app.apk"), "com.example.app")

    @mock.patch("src.apk_identity.read_identity")
    def test_rejects_version_name_mismatch(self, read_identity_mock: mock.Mock) -> None:
        read_identity_mock.return_value = ApkIdentity("com.example.app", "9.9.9", "999")
        with self.assertRaisesRegex(ApkIdentityError, "version mismatch"):
            validate_identity(Path("app.apk"), "com.example.app", VersionCandidate(name="1.2.3"))

    @mock.patch("src.apk_identity.read_identity")
    def test_rejects_version_code_mismatch_when_known(self, read_identity_mock: mock.Mock) -> None:
        read_identity_mock.return_value = ApkIdentity("com.example.app", "1.2.3", "999")
        with self.assertRaisesRegex(ApkIdentityError, "version mismatch"):
            validate_identity(Path("app.apk"), "com.example.app", VersionCandidate(name="1.2.3", code="123"))

    @mock.patch("src.apk_identity.contains_japanese", return_value=False)
    @mock.patch("src.apk_identity.read_identity")
    def test_provider_chain_can_accept_missing_japanese(
        self, read_identity_mock: mock.Mock, _ja: mock.Mock
    ) -> None:
        read_identity_mock.return_value = ApkIdentity(
            "com.example.app", "1.2.3", "123"
        )
        result = validate_identity(
            Path("app.apk"),
            "com.example.app",
            VersionCandidate(name="1.2.3", code="123"),
            require_japanese=False,
        )
        self.assertEqual(result.version_name, "1.2.3")


if __name__ == "__main__":
    unittest.main()
