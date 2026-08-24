from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

from scripts import download_apks
from src.versioning import VersionCandidate


class CurrentPlayCacheReuseTests(unittest.TestCase):
    def test_unrestricted_policy_checks_current_play_identity_then_cache(self) -> None:
        current = VersionCandidate(name="9.8.7", code="987")
        cached = Path("cached-example-v9.8.7.apk")
        with mock.patch.object(
            download_apks.google_play_metadata,
            "current_release_identity",
            return_value=current,
        ) as metadata, mock.patch.object(
            download_apks,
            "_restore_cached_candidate",
            return_value=(cached, "9.8.7"),
        ) as restore:
            result = download_apks._restore_current_play_cache(
                "example", "example.package", "arm64-v8a", []
            )

        self.assertEqual(result, (cached, "9.8.7"))
        metadata.assert_called_once_with("example.package")
        restore.assert_called_once_with(
            "example", "example.package", "arm64-v8a", [current]
        )

    def test_exact_patch_version_does_not_probe_current_play_release(self) -> None:
        exact = VersionCandidate(name="1.2.3", code="123")
        with mock.patch.object(
            download_apks.google_play_metadata,
            "current_release_identity",
        ) as metadata:
            result = download_apks._restore_current_play_cache(
                "example", "example.package", "arm64-v8a", [exact]
            )

        self.assertIsNone(result)
        metadata.assert_not_called()


if __name__ == "__main__":
    unittest.main()
