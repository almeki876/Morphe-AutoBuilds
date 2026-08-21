from __future__ import annotations

import unittest
from unittest.mock import patch

from src import apkpure
from src.versioning import VersionCandidate


class ApkPureTests(unittest.TestCase):
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
