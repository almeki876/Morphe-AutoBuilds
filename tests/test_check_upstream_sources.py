from __future__ import annotations

import json
import unittest
from unittest import mock

from scripts import check_upstream_sources


class _Response:
    def __init__(self, payload: object) -> None:
        self._payload = json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def read(self) -> bytes:
        return self._payload


class CheckUpstreamSourcesTests(unittest.TestCase):
    def test_latest_github_tag_skips_newer_apk_only_release(self) -> None:
        releases = [
            {
                "tag_name": "diagnostic-build",
                "published_at": "2026-09-18T00:00:00Z",
                "assets": [{"name": "diagnostic.apk"}],
            },
            {
                "tag_name": "v1.37.2",
                "published_at": "2026-09-17T00:00:00Z",
                "assets": [{"name": "patches-1.37.2.mpp"}],
            },
        ]

        with mock.patch.object(
            check_upstream_sources,
            "urlopen",
            return_value=_Response(releases),
        ):
            tag = check_upstream_sources.latest_tag("owner", "repo")

        self.assertEqual(tag, "v1.37.2")

    def test_patch_jar_is_a_valid_bundle_asset(self) -> None:
        self.assertTrue(check_upstream_sources._is_patch_asset_name("patches-1.0.jar"))
        self.assertFalse(check_upstream_sources._is_patch_asset_name("tool-1.0.jar"))


if __name__ == "__main__":
    unittest.main()
