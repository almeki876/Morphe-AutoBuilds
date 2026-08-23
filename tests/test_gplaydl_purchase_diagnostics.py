import os
import sys
import types
import unittest
from types import SimpleNamespace
from unittest import mock

from src import gplaydl_purchase_diagnostics as diagnostics


class GPlayDlPurchaseDiagnosticsTests(unittest.TestCase):
    def test_safe_human_strings_keeps_messages_and_drops_credentials(self) -> None:
        values = [
            "This item is not available in your country.",
            "morpheautobuilds@gmail.com",
            "https://android.clients.google.com/fdfe/purchase",
            "aas_et/" + "x" * 80,
            "A" * 80,
            "opaqueIdentifier",
            "This item is not available in your country.",
        ]

        self.assertEqual(
            diagnostics._safe_human_strings(values),
            ["This item is not available in your country."],
        )

    def test_safe_human_strings_honors_limit(self) -> None:
        values = [f"Message number {i}" for i in range(10)]
        self.assertEqual(len(diagnostics._safe_human_strings(values, limit=3)), 3)

    def test_safe_response_preview_redacts_credentials(self) -> None:
        body = (
            b"Bad request for morpheautobuilds@gmail.com "
            b"Bearer ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789 "
            b"aas_et/abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 "
            b"https://android.clients.google.com/fdfe/purchase"
        )
        preview = diagnostics._safe_response_preview(body)

        self.assertIn("Bad request", preview)
        self.assertNotIn("morpheautobuilds@gmail.com", preview)
        self.assertNotIn("aas_et/", preview)
        self.assertNotIn("android.clients.google.com", preview)
        self.assertIn("[redacted-email]", preview)
        self.assertIn("[redacted-aas-token]", preview)

    def test_safe_response_preview_honors_limit(self) -> None:
        preview = diagnostics._safe_response_preview(b"A " * 500, limit=40)
        self.assertLessEqual(len(preview), 41)
        self.assertTrue(preview.endswith("…"))

    def test_priority_diagnostic_mints_each_profile_and_reports_only_status(self) -> None:
        class PlayAPIError(Exception):
            pass

        class AppNotPurchasedError(PlayAPIError):
            pass

        class AppNotSupportedError(PlayAPIError):
            pass

        class AppNotAvailableError(PlayAPIError):
            pass

        package = types.ModuleType("gplaydl")
        package.__path__ = []
        api = types.ModuleType("gplaydl.api")
        auth = types.ModuleType("gplaydl.auth")
        profiles = types.ModuleType("gplaydl.profiles")

        api.PlayAPIError = PlayAPIError
        api.AppNotPurchasedError = AppNotPurchasedError
        api.AppNotSupportedError = AppNotSupportedError
        api.AppNotAvailableError = AppNotAvailableError
        api.DETAILS_URL = "https://play.invalid/details"
        api.PURCHASE_URL = "https://play.invalid/purchase"
        api.DELIVERY_URL = "https://play.invalid/delivery"
        api.httpx = SimpleNamespace(
            get=mock.Mock(return_value=SimpleNamespace(status_code=200)),
            post=mock.Mock(return_value=SimpleNamespace(status_code=400)),
        )

        def get_details(_package_name, _auth_data):
            api.httpx.get(api.DETAILS_URL + "?doc=jp.example.bank")
            return SimpleNamespace(version_code=40)

        def purchase(_package_name, _version_code, _auth_data):
            api.httpx.post(api.PURCHASE_URL)
            return ""

        def get_delivery(_package_name, _version_code, auth_data, _token):
            api.httpx.get(api.DELIVERY_URL + "?doc=jp.example.bank")
            if auth_data["profile"] == "first":
                raise AppNotPurchasedError("secret response body")
            return SimpleNamespace(download_url="https://play.invalid/apk")

        api.get_details = get_details
        api.purchase = purchase
        api.get_delivery = get_delivery
        profile_values = [
            ("first", {"UserReadableName": "First Phone"}),
            ("second", {"UserReadableName": "Second Phone"}),
        ]
        profiles.get_priority_profiles = mock.Mock(return_value=profile_values)
        auth.fetch_token_for_profile = mock.Mock(
            side_effect=lambda profile, **_kwargs: {
                "profile": "first" if profile is profile_values[0][1] else "second",
                "authToken": "secret-auth-token",
                "email": "secret@example.com",
            }
        )
        modules = {
            "gplaydl": package,
            "gplaydl.api": api,
            "gplaydl.auth": auth,
            "gplaydl.profiles": profiles,
        }
        package.api = api
        package.auth = auth
        package.profiles = profiles

        with (
            mock.patch.dict(sys.modules, modules),
            mock.patch.dict(
                os.environ,
                {
                    "GPLAYDL_DISPENSER_URL": "http://127.0.0.1:18080",
                    "GPLAY_EMAIL": "secret@example.com",
                },
                clear=False,
            ),
            self.assertLogs(level="ERROR") as logs,
        ):
            results = diagnostics.diagnose_priority_profiles("jp.example.bank")

        self.assertEqual(auth.fetch_token_for_profile.call_count, 2)
        self.assertEqual([item["profile_key"] for item in results], ["first", "second"])
        self.assertEqual(results[0]["delivery_status"], 3)
        self.assertFalse(results[0]["success"])
        self.assertEqual(results[1]["delivery_status"], 1)
        self.assertTrue(results[1]["success"])
        rendered = "\n".join(logs.output)
        self.assertIn("preferred_profile=second", rendered)
        self.assertNotIn("secret@example.com", rendered)
        self.assertNotIn("secret-auth-token", rendered)
        self.assertNotIn("secret response body", rendered)


if __name__ == "__main__":
    unittest.main()
