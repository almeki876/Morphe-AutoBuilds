from __future__ import annotations

import unittest
from unittest.mock import patch

from src import apkpure
from src.versioning import VersionCandidate


class ApkPureTests(unittest.TestCase):
    def test_latest_version_uses_highest_history_version_code(self):
        rows = [
            {
                "package_name": "app.ninjavpn.android",
                "version_name": "1.4.8",
                "version_code": "45",
            },
            {
                "package_name": "app.ninjavpn.android",
                "version_name": "1.4.9",
                "version_code": "46",
            },
            # The API can return duplicate assets for the same release.
            {
                "package_name": "app.ninjavpn.android",
                "version_name": "1.4.9",
                "version_code": "46",
            },
        ]

        with patch("src.apkpure._history_entries", return_value=rows):
            version = apkpure.get_latest_version(
                "ninja-vpn",
                {"name": "ninja-vpn-fast-secure-vpn", "package": "app.ninjavpn.android"},
            )

        self.assertEqual(version, "1.4.9")

    def test_history_download_requires_exact_identity_and_trusted_host(self):
        rows = [
            {
                "package_name": "app.ninjavpn.android",
                "version_name": "1.4.8",
                "version_code": "45",
                "asset": {"url": "https://data.winudf.com/XAPK/wrong"},
            },
            {
                "package_name": "app.ninjavpn.android",
                "version_name": "1.4.9",
                "version_code": "46",
                "asset": {"url": "https://winudf.com.example.org/XAPK/untrusted"},
            },
            {
                "package_name": "app.ninjavpn.android",
                "version_name": "1.4.9",
                "version_code": "46",
                "asset": {"url": "https://data.winudf.com/XAPK/exact?token=test"},
            },
        ]

        with patch("src.apkpure._history_entries", return_value=rows):
            url = apkpure._history_download_for_candidate(
                VersionCandidate(name="1.4.9", code="46"),
                "ninja-vpn",
                {"package": "app.ninjavpn.android"},
            )

        self.assertEqual(
            url,
            "https://data.winudf.com/XAPK/exact?token=test",
        )

    def test_history_download_rejects_wrong_package(self):
        rows = [
            {
                "package_name": "app.attacker.android",
                "version_name": "1.4.9",
                "version_code": "46",
                "asset": {"url": "https://data.winudf.com/XAPK/wrong-package"},
            }
        ]

        with patch("src.apkpure._history_entries", return_value=rows):
            url = apkpure._history_download_for_candidate(
                VersionCandidate(name="1.4.9", code="46"),
                "ninja-vpn",
                {"package": "app.ninjavpn.android"},
            )

        self.assertIsNone(url)

    def test_version_code_endpoint_accepts_matching_version_name_filename(self):
        candidate = VersionCandidate(name="26.08.01", code="262512929")
        config = {"package": "com.adobe.scan.android"}

        with patch(
            "src.apkpure._probe_direct_endpoint",
            return_value="Adobe Scan AI PDF Scanner, OCR_26.08.01_APKPure.xapk",
        ):
            url = apkpure._direct_download_for_candidate(
                candidate, "adobe-scan", config
            )

        self.assertEqual(
            url,
            "https://d.apkpure.net/b/APK/com.adobe.scan.android?versionCode=262512929",
        )

    def test_version_code_endpoint_rejects_wrong_version_filename(self):
        candidate = VersionCandidate(name="26.08.01", code="262512929")
        config = {"package": "com.adobe.scan.android"}

        with patch(
            "src.apkpure._probe_direct_endpoint",
            return_value="Adobe Scan AI PDF Scanner, OCR_26.08.10_APKPure.xapk",
        ):
            url = apkpure._direct_download_for_candidate(
                candidate, "adobe-scan", config
            )

        self.assertIsNone(url)


if __name__ == "__main__":
    unittest.main()
