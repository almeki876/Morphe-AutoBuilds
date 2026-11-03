"""The CLI follows GitHub's ``releases/latest``; patch bundles follow the newest release."""

import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest import mock

from src import utils


def _release(tag: str, day: int, *, prerelease=False, draft=False):
    return SimpleNamespace(
        tag_name=tag,
        prerelease=prerelease,
        draft=draft,
        created_at=datetime(2026, 11, day, tzinfo=timezone.utc),
        raw_data={"tag_name": tag},
    )


class ReleaseChannelTests(unittest.TestCase):
    RELEASES = [
        _release("v2.0.0-dev.4", 3, prerelease=True),
        _release("v2.0.0-dev.3", 2, prerelease=True),
        _release("v1.20.1", 1, draft=True),
        _release("v1.20.0", 1),
        _release("v1.19.1", 1),
    ]

    def _resolve(self, include_prereleases: bool) -> str:
        repo = mock.Mock()
        repo.get_releases.return_value = self.RELEASES
        with mock.patch.object(utils.gh, "get_repo", return_value=repo):
            return utils._detect_github_release_once(
                "o", "r", "latest", include_prereleases=include_prereleases
            )["tag_name"]

    def test_cli_channel_is_the_newest_stable_release(self) -> None:
        # Pre-releases and drafts are never selected, however new they are.
        self.assertEqual(self._resolve(include_prereleases=False), "v1.20.0")

    def test_patch_channel_still_follows_the_newest_release(self) -> None:
        self.assertEqual(self._resolve(include_prereleases=True), "v2.0.0-dev.4")


if __name__ == "__main__":
    unittest.main()
