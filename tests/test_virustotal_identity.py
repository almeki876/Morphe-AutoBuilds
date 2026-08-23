from __future__ import annotations

import os
import unittest
from unittest import mock

from src import virustotal_identity
from src.versioning import VersionCandidate


class VirusTotalIdentityTests(unittest.TestCase):
    def setUp(self) -> None:
        env = mock.patch.dict(os.environ, {"VIRUSTOTAL_API_KEY": "test-key"})
        env.start()
        self.addCleanup(env.stop)

    @staticmethod
    def _response(payload: dict, status: int = 200) -> mock.Mock:
        response = mock.Mock(status_code=status)
        response.json.return_value = payload
        return response

    @mock.patch("src.virustotal_identity._api_get")
    def test_xapk_bundled_apk_manifest_resolves_identity(
        self,
        api_get: mock.Mock,
    ) -> None:
        container = self._response({"data": {"attributes": {"type_tag": "zip"}}})
        bundled = self._response(
            {
                "data": [
                    {
                        "attributes": {
                            "androguard": {
                                "Package": "com.amazon.mShop.android.shopping",
                                "AndroidVersionName": "32.13.2.100",
                                "AndroidVersionCode": "1241320216",
                            }
                        }
                    }
                ]
            }
        )
        api_get.side_effect = [container, bundled]

        self.assertEqual(
            virustotal_identity.identities_for_sha256(
                "a" * 64,
                "com.amazon.mShop.android.shopping",
            ),
            [VersionCandidate(name="32.13.2.100", code="1241320216")],
        )
        self.assertEqual(api_get.call_count, 2)
        self.assertIn("/bundled_files?limit=40", api_get.call_args_list[1].args[0])

    @mock.patch("src.virustotal_identity._api_get")
    def test_other_package_identity_is_rejected(self, api_get: mock.Mock) -> None:
        api_get.side_effect = [
            self._response(
                {
                    "data": {
                        "attributes": {
                            "androguard": {
                                "Package": "com.example.other",
                                "AndroidVersionName": "32.13.2.100",
                                "AndroidVersionCode": "1241320216",
                            }
                        }
                    }
                }
            ),
            self._response({"data": []}),
        ]

        self.assertEqual(
            virustotal_identity.identities_for_sha256(
                "b" * 64,
                "com.amazon.mShop.android.shopping",
            ),
            [],
        )

    @mock.patch("src.virustotal_identity._api_get")
    def test_invalid_hash_never_contacts_api(self, api_get: mock.Mock) -> None:
        self.assertEqual(
            virustotal_identity.identities_for_sha256(
                "not-a-sha256",
                "com.example.app",
            ),
            [],
        )
        api_get.assert_not_called()


if __name__ == "__main__":
    unittest.main()
