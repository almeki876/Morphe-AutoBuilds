import os
import unittest
from types import SimpleNamespace
from unittest import mock

from src import google_play_metadata
from src.versioning import VersionCandidate


class GooglePlayMetadataTests(unittest.TestCase):
    def setUp(self) -> None:
        self.api_key = mock.patch.dict(
            os.environ,
            {
                "GPLAYDL_API_KEY": "test-key",
                "GPLAYDL_ARCH": "",
                "GPLAYDL_DISPENSER_URL": "",
                "GPLAYDL_EMAIL": "",
                "GPLAY_EMAIL": "",
                "GPLAY_AAS_TOKEN": "",
            },
        )
        self.api_key.start()
        self.addCleanup(self.api_key.stop)

    @mock.patch("src.google_play_metadata._current_details")
    @mock.patch("src.google_play_metadata._profile_details")
    @mock.patch("src.google_play_metadata.local_gplaydl_dispenser.ensure_running")
    def test_current_release_identity_returns_name_and_code_without_download(
        self,
        ensure_running: mock.Mock,
        profile_details: mock.Mock,
        current_details: mock.Mock,
    ) -> None:
        current_details.return_value = SimpleNamespace(
            package="com.example.app",
            version_string="9.8.7",
            version_code=98742,
        )

        current = google_play_metadata.current_release_identity("com.example.app")

        self.assertEqual(current, VersionCandidate(name="9.8.7", code="98742"))
        ensure_running.assert_called_once_with()
        current_details.assert_called_once_with("com.example.app", "arm64", None, None)
        profile_details.assert_not_called()

    @mock.patch("src.google_play_metadata._current_details")
    @mock.patch("src.google_play_metadata._profile_details")
    @mock.patch("src.google_play_metadata.local_gplaydl_dispenser.ensure_running")
    def test_current_release_identity_uses_profile_if_primary_is_incomplete(
        self,
        ensure_running: mock.Mock,
        profile_details: mock.Mock,
        current_details: mock.Mock,
    ) -> None:
        current_details.return_value = None
        profile_details.return_value = [
            SimpleNamespace(
                package="com.example.app",
                version_string="9.8.7",
                version_code=98742,
            )
        ]

        self.assertEqual(
            google_play_metadata.current_release_identity("com.example.app"),
            VersionCandidate(name="9.8.7", code="98742"),
        )
        profile_details.assert_called_once_with("com.example.app", "arm64", None, None)

    @mock.patch("src.google_play_metadata.local_gplaydl_dispenser.ensure_running")
    def test_aas_credentials_can_bootstrap_metadata_context(
        self,
        ensure_running: mock.Mock,
    ) -> None:
        def bootstrap() -> bool:
            os.environ["GPLAYDL_API_KEY"] = "generated-key"
            os.environ["GPLAYDL_DISPENSER_URL"] = "http://127.0.0.1:18080"
            return True

        ensure_running.side_effect = bootstrap
        with mock.patch.dict(
            os.environ,
            {
                "GPLAYDL_API_KEY": "",
                "GPLAY_EMAIL": "user@example.invalid",
                "GPLAY_AAS_TOKEN": "aas_et/test",
                "GPLAYDL_DISPENSER_URL": "",
            },
        ):
            self.assertEqual(
                google_play_metadata._context(),
                ("arm64", "http://127.0.0.1:18080", "user@example.invalid"),
            )
        ensure_running.assert_called_once_with()

    @mock.patch("src.google_play_metadata._current_details")
    @mock.patch("src.google_play_metadata._profile_details")
    @mock.patch("src.google_play_metadata.local_gplaydl_dispenser.ensure_running")
    def test_exact_current_release_adds_version_code(
        self,
        ensure_running: mock.Mock,
        profile_details: mock.Mock,
        current_details: mock.Mock,
    ) -> None:
        current_details.return_value = SimpleNamespace(
            package="com.example.app",
            version_string="32.13.2.100",
            version_code=1241320216,
        )
        requested = VersionCandidate(
            name="32.13.2.100",
            raw="32.13.2.100 (9 patches)",
        )

        resolved = google_play_metadata.resolve_candidate_identities(
            "com.example.app", [requested]
        )

        self.assertEqual(
            resolved,
            [
                VersionCandidate(
                    name="32.13.2.100",
                    code="1241320216",
                    raw=requested.raw,
                )
            ],
        )
        ensure_running.assert_called_once_with()
        current_details.assert_called_once_with(
            "com.example.app", "arm64", None, None
        )
        profile_details.assert_not_called()

    @mock.patch("src.google_play_metadata._current_details")
    @mock.patch("src.google_play_metadata._profile_details")
    @mock.patch("src.google_play_metadata.local_gplaydl_dispenser.ensure_running")
    def test_different_current_release_is_not_substituted(
        self,
        ensure_running: mock.Mock,
        profile_details: mock.Mock,
        current_details: mock.Mock,
    ) -> None:
        current_details.return_value = SimpleNamespace(
            package="com.example.app",
            version_string="32.13.0.100",
            version_code=1241320016,
        )
        requested = VersionCandidate(name="32.13.2.100", raw="32.13.2.100")
        profile_details.return_value = []

        self.assertEqual(
            google_play_metadata.resolve_candidate_identities(
                "com.example.app", [requested]
            ),
            [requested],
        )

    @mock.patch("src.google_play_metadata._current_details")
    @mock.patch("src.google_play_metadata._profile_details")
    @mock.patch("src.google_play_metadata.local_gplaydl_dispenser.ensure_running")
    def test_device_specific_current_release_adds_version_code(
        self,
        ensure_running: mock.Mock,
        profile_details: mock.Mock,
        current_details: mock.Mock,
    ) -> None:
        current_details.return_value = SimpleNamespace(
            package="com.example.app",
            version_string="32.13.0.100",
            version_code=1241320016,
        )
        profile_details.return_value = [
            SimpleNamespace(
                package="com.example.app",
                version_string="32.13.2.100",
                version_code=1241320216,
            )
        ]
        requested = VersionCandidate(name="32.13.2.100", raw="32.13.2.100")

        self.assertEqual(
            google_play_metadata.resolve_candidate_identities(
                "com.example.app", [requested]
            ),
            [
                VersionCandidate(
                    name="32.13.2.100",
                    code="1241320216",
                    raw=requested.raw,
                )
            ],
        )
        profile_details.assert_called_once_with(
            "com.example.app", "arm64", None, None
        )

    @mock.patch("src.google_play_metadata._current_details")
    @mock.patch("src.google_play_metadata._profile_details")
    @mock.patch("src.google_play_metadata.local_gplaydl_dispenser.ensure_running")
    def test_profile_lookup_failure_keeps_explicit_release_unresolved(
        self,
        ensure_running: mock.Mock,
        profile_details: mock.Mock,
        current_details: mock.Mock,
    ) -> None:
        current_details.return_value = SimpleNamespace(
            package="com.example.app",
            version_string="32.13.0.100",
            version_code=1241320016,
        )
        profile_details.side_effect = RuntimeError("dispenser unavailable")
        requested = VersionCandidate(name="32.13.2.100", raw="32.13.2.100")

        self.assertEqual(
            google_play_metadata.resolve_candidate_identities(
                "com.example.app", [requested]
            ),
            [requested],
        )

    @mock.patch("src.google_play_metadata.local_gplaydl_dispenser.ensure_running")
    def test_missing_credentials_does_not_contact_play(
        self,
        ensure_running: mock.Mock,
    ) -> None:
        requested = VersionCandidate(name="1.2.3", raw="1.2.3")
        with mock.patch.dict(
            os.environ,
            {
                "GPLAYDL_API_KEY": "",
                "GPLAY_EMAIL": "",
                "GPLAYDL_EMAIL": "",
                "GPLAY_AAS_TOKEN": "",
            },
        ):
            self.assertIsNone(
                google_play_metadata.current_release_identity("com.example.app")
            )
            self.assertEqual(
                google_play_metadata.resolve_candidate_identities(
                    "com.example.app", [requested]
                ),
                [requested],
            )
        ensure_running.assert_not_called()


if __name__ == "__main__":
    unittest.main()
