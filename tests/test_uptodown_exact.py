from __future__ import annotations

import unittest
from unittest import mock

from src import providers, uptodown_exact
from src.versioning import VersionCandidate


class UptodownExactTests(unittest.TestCase):
    def test_entry_matches_separate_version_name_and_code(self) -> None:
        entry = {
            "version": "32.13.2.100",
            "versionCode": 1241320216,
            "fileID": 99,
        }
        candidate = VersionCandidate(
            name="32.13.2.100",
            code="1241320216",
        )

        self.assertTrue(uptodown_exact._entry_matches_candidate(entry, candidate))

    def test_entry_rejects_same_name_with_wrong_version_code(self) -> None:
        entry = {
            "version": "32.13.2.100",
            "versionCode": 1241320016,
            "fileID": 99,
        }
        candidate = VersionCandidate(
            name="32.13.2.100",
            code="1241320216",
        )

        self.assertFalse(uptodown_exact._entry_matches_candidate(entry, candidate))

    @mock.patch("src.uptodown_exact.legacy._api_get")
    @mock.patch("src.uptodown_exact.legacy._api_app_id", return_value="12345")
    def test_resolve_candidate_identities_enriches_patch_name_with_live_code(
        self,
        api_app_id: mock.Mock,
        api_get: mock.Mock,
    ) -> None:
        versions = mock.Mock(status_code=200)
        versions.json.return_value = {
            "data": [
                {
                    "version": "32.13.2.100",
                    "versionCode": 1241320216,
                    "fileID": 67890,
                }
            ]
        }
        api_get.return_value = versions

        resolved = uptodown_exact.resolve_candidate_identities(
            "com.amazon.mShop.android.shopping",
            [VersionCandidate(name="32.13.2.100")],
        )

        self.assertEqual(
            resolved,
            [VersionCandidate(name="32.13.2.100", code="1241320216")],
        )
        api_app_id.assert_called_once_with("com.amazon.mShop.android.shopping")

    @mock.patch("src.uptodown_exact.legacy._api_get")
    @mock.patch("src.uptodown_exact.legacy._api_app_id", return_value="12345")
    def test_resolve_candidate_identities_recovers_name_from_patch_version_code(
        self,
        api_app_id: mock.Mock,
        api_get: mock.Mock,
    ) -> None:
        versions = mock.Mock(status_code=200)
        versions.json.return_value = {
            "data": [
                {
                    "version": "8.8.6",
                    "versionCode": 88600,
                    "fileID": 42,
                }
            ]
        }
        api_get.return_value = versions

        resolved = uptodown_exact.resolve_candidate_identities(
            "com.teslacoilsw.launcher",
            [VersionCandidate(name="88600", code="88600")],
        )

        self.assertEqual(
            resolved,
            [VersionCandidate(name="8.8.6", code="88600")],
        )
        api_app_id.assert_called_once_with("com.teslacoilsw.launcher")

    @mock.patch("src.uptodown_exact.legacy._api_get")
    @mock.patch("src.uptodown_exact.legacy._api_app_id", return_value="12345")
    def test_exact_api_paginates_until_matching_release(
        self,
        api_app_id: mock.Mock,
        api_get: mock.Mock,
    ) -> None:
        first_page = mock.Mock(status_code=200)
        first_page.json.return_value = {
            "data": [
                {
                    "version": f"31.0.{index}.100",
                    "versionCode": 1000000000 + index,
                    "fileID": index + 1,
                }
                for index in range(50)
            ]
        }
        second_page = mock.Mock(status_code=200)
        second_page.json.return_value = {
            "data": [
                {
                    "version": "32.13.2.100",
                    "versionCode": 1241320216,
                    "fileID": 67890,
                }
            ]
        }
        download_response = mock.Mock(status_code=200)
        download_response.json.return_value = {
            "data": {
                "downloadURL": "https://dw.uptodown.com/dwn/exact-token"
            }
        }
        api_get.side_effect = [first_page, second_page, download_response]

        link = uptodown_exact._exact_api_download_link(
            "com.amazon.mShop.android.shopping",
            VersionCandidate(
                name="32.13.2.100",
                code="1241320216",
            ),
        )

        self.assertEqual(link, "https://dw.uptodown.com/dwn/exact-token")
        api_app_id.assert_called_once_with("com.amazon.mShop.android.shopping")
        self.assertIn("page[offset]=0", api_get.call_args_list[0].args[0])
        self.assertIn("page[offset]=50", api_get.call_args_list[1].args[0])
        self.assertEqual(
            api_get.call_args_list[2].args[0],
            "/apps/12345/file/67890/downloadUrl?update=0",
        )

    @mock.patch("src.uptodown_exact.legacy._api_get")
    @mock.patch("src.uptodown_exact.legacy._api_app_id", return_value="12345")
    def test_exact_api_never_substitutes_nearby_release(
        self,
        api_app_id: mock.Mock,
        api_get: mock.Mock,
    ) -> None:
        versions = mock.Mock(status_code=200)
        versions.json.return_value = {
            "data": [
                {
                    "version": "32.13.0.100",
                    "versionCode": 1241320016,
                    "fileID": 11111,
                }
            ]
        }
        api_get.return_value = versions

        link = uptodown_exact._exact_api_download_link(
            "com.amazon.mShop.android.shopping",
            VersionCandidate(
                name="32.13.2.100",
                code="1241320216",
            ),
        )

        self.assertIsNone(link)
        self.assertEqual(api_get.call_count, 1)

    def test_download_url_must_stay_on_uptodown_domains(self) -> None:
        self.assertEqual(
            uptodown_exact._safe_download_url(
                "https://dw.uptodown.com/dwn/exact-token"
            ),
            "https://dw.uptodown.com/dwn/exact-token",
        )
        self.assertIsNone(
            uptodown_exact._safe_download_url("https://example.com/fake.apk")
        )

    def test_provider_registry_uses_exact_wrapper(self) -> None:
        self.assertIs(providers.MODULES["uptodown"], uptodown_exact)

    @mock.patch(
        "src.uptodown_exact.fallback.get_download_link_for_candidate",
        return_value="https://dw.uptodown.com/dwn/fallback-token",
    )
    @mock.patch(
        "src.uptodown_exact._exact_api_download_link",
        side_effect=RuntimeError("temporary eAPI failure"),
    )
    def test_provider_falls_back_without_weakening_candidate(
        self,
        exact_api: mock.Mock,
        fallback: mock.Mock,
    ) -> None:
        candidate = VersionCandidate(name="11.4.5")
        config = {
            "name": "adobe-lightroom-mobile",
            "package": "com.adobe.lrmobile",
        }

        link = uptodown_exact.get_download_link_for_candidate(
            candidate,
            "lightroom",
            config,
        )

        self.assertEqual(link, "https://dw.uptodown.com/dwn/fallback-token")
        exact_api.assert_called_once_with("com.adobe.lrmobile", candidate)
        fallback.assert_called_once_with(candidate, "lightroom", config)


if __name__ == "__main__":
    unittest.main()
