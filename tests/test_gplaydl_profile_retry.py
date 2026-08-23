import os
import sys
import types
import unittest
from types import SimpleNamespace
from unittest import mock

from src import gplaydl_profile_retry as retry


class PlayAPIError(Exception):
    pass


class AppNotPurchasedError(PlayAPIError):
    pass


class AppNotSupportedError(PlayAPIError):
    pass


class AppNotAvailableError(PlayAPIError):
    pass


class DispenserError(Exception):
    pass


def fake_gplaydl_modules(profiles):
    package = types.ModuleType("gplaydl")
    package.__path__ = []
    package.__version__ = retry.SUPPORTED_GPLAYDL_VERSION

    api = types.ModuleType("gplaydl.api")
    api.PlayAPIError = PlayAPIError
    api.AppNotPurchasedError = AppNotPurchasedError
    api.AppNotSupportedError = AppNotSupportedError
    api.AppNotAvailableError = AppNotAvailableError
    api.get_details = mock.Mock(return_value=SimpleNamespace(version_code=40))
    api.purchase = mock.Mock(return_value="")
    api.get_delivery = mock.Mock()

    auth = types.ModuleType("gplaydl.auth")
    auth.DispenserError = DispenserError
    auth.fetch_token_for_profile = mock.Mock(
        side_effect=lambda profile, **_kwargs: {
            "profile": profile["UserReadableName"],
            "authToken": "secret-auth-token",
            "email": "secret@example.com",
        }
    )

    profile_module = types.ModuleType("gplaydl.profiles")
    profile_module.get_priority_profiles = mock.Mock(return_value=profiles)

    package.api = api
    package.auth = auth
    package.profiles = profile_module
    return {
        "gplaydl": package,
        "gplaydl.api": api,
        "gplaydl.auth": auth,
        "gplaydl.profiles": profile_module,
    }, api, auth


class GPlayDlProfileRetryTests(unittest.TestCase):
    def test_preferred_profile_moves_to_front_without_dropping_profiles(self) -> None:
        profiles = [("one", {}), ("two", {}), ("three", {})]
        self.assertEqual(
            [key for key, _ in retry.ordered_priority_profiles(profiles, "two")],
            ["two", "one", "three"],
        )

    def test_not_purchased_uses_fresh_auth_for_each_profile_until_success(self) -> None:
        profiles = [
            ("first", {"UserReadableName": "First Phone"}),
            ("second", {"UserReadableName": "Second Phone"}),
        ]
        modules, api, auth = fake_gplaydl_modules(profiles)
        delivery = SimpleNamespace(download_url="https://play.invalid/file")
        api.get_delivery.side_effect = [AppNotPurchasedError("no"), delivery]

        with (
            mock.patch.dict(sys.modules, modules),
            mock.patch.dict(os.environ, {retry.PREFERRED_PROFILE_ENV: ""}, clear=False),
        ):
            details, version_code, result = retry.acquire_after_not_purchased(
                "jp.example.bank",
                None,
                "arm64",
                "http://127.0.0.1:18080",
                "secret@example.com",
            )

        self.assertEqual(details.version_code, 40)
        self.assertEqual(version_code, 40)
        self.assertIs(result, delivery)
        self.assertEqual(auth.fetch_token_for_profile.call_count, 2)
        self.assertEqual(
            [
                call.args[0]["UserReadableName"]
                for call in auth.fetch_token_for_profile.call_args_list
            ],
            ["First Phone", "Second Phone"],
        )
        self.assertEqual(api.purchase.call_count, 2)
        self.assertEqual(api.get_delivery.call_count, 2)

    def test_all_profile_failure_is_secret_safe(self) -> None:
        profiles = [
            ("first", {"UserReadableName": "First Phone"}),
            ("second", {"UserReadableName": "Second Phone"}),
        ]
        modules, api, auth = fake_gplaydl_modules(profiles)
        api.get_delivery.side_effect = AppNotPurchasedError("secret backend body")

        with (
            mock.patch.dict(sys.modules, modules),
            mock.patch.dict(os.environ, {retry.PREFERRED_PROFILE_ENV: ""}, clear=False),
            self.assertRaises(AppNotPurchasedError) as raised,
        ):
            retry.acquire_after_not_purchased(
                "jp.example.bank",
                None,
                "arm64",
                "http://127.0.0.1:18080",
                "secret@example.com",
            )

        message = str(raised.exception)
        self.assertIn("first:not-purchased", message)
        self.assertIn("second:not-purchased", message)
        self.assertNotIn("secret@example.com", message)
        self.assertNotIn("secret-auth-token", message)
        self.assertNotIn("secret backend body", message)
        self.assertEqual(auth.fetch_token_for_profile.call_count, 2)

    def test_cli_patch_is_guarded_and_retries_only_not_purchased(self) -> None:
        modules, _api, _auth = fake_gplaydl_modules([])
        cli = types.ModuleType("gplaydl.cli")

        def original(
            package,
            version,
            arch,
            auth_data,
            dispenser,
            email,
            locales=None,
        ):
            raise AppNotPurchasedError("initial")

        cli._acquire = original
        cli.rprint = mock.Mock()
        modules["gplaydl.cli"] = cli
        modules["gplaydl"].cli = cli
        fallback = (SimpleNamespace(version_code=40), 40, SimpleNamespace(download_url="url"))

        with (
            mock.patch.dict(sys.modules, modules),
            mock.patch.object(
                retry.metadata,
                "version",
                return_value=retry.SUPPORTED_GPLAYDL_VERSION,
            ),
            mock.patch.object(
                retry,
                "acquire_after_not_purchased",
                return_value=fallback,
            ) as acquire,
        ):
            retry.install_cli_patch()
            result = cli._acquire(
                "jp.example.bank",
                None,
                "arm64",
                {"authToken": "secret"},
                "http://127.0.0.1:18080",
                "secret@example.com",
            )

        self.assertIs(result, fallback)
        acquire.assert_called_once_with(
            "jp.example.bank",
            None,
            "arm64",
            "http://127.0.0.1:18080",
            "secret@example.com",
            None,
        )


if __name__ == "__main__":
    unittest.main()
