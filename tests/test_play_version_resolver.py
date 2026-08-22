import types
import unittest
from unittest.mock import patch

from src import apkpure, play_version_resolver, providers, versioning
from src.versioning import VersionCandidate


class PlayVersionResolverTests(unittest.TestCase):
    def setUp(self) -> None:
        versioning._DISCOVERED_VERSION_CODES.clear()

    def tearDown(self) -> None:
        versioning._DISCOVERED_VERSION_CODES.clear()

    def test_any_uses_current_play_release(self) -> None:
        self.assertIsNone(play_version_resolver.resolve_candidate("com.example.app", None))

    def test_reuses_runtime_discovered_version_code(self) -> None:
        versioning.remember_version_code("com.example.app", "2.3.4", "23401")
        resolved = play_version_resolver.resolve_candidate(
            "com.example.app",
            VersionCandidate(name="2.3.4", raw="2.3.4 (4 patches)"),
        )
        self.assertEqual(resolved.name, "2.3.4")
        self.assertEqual(resolved.code, "23401")

    def test_uses_any_provider_that_implements_identity_resolver(self) -> None:
        def resolver(package, candidates):
            self.assertEqual(package, "com.example.app")
            self.assertEqual([candidate.name for candidate in candidates], ["9.8.7"])
            return [VersionCandidate(name="9.8.7", code="98742")]

        fake_module = types.SimpleNamespace(resolve_candidate_identities=resolver)
        modules = dict(providers.MODULES)
        modules["future-provider"] = fake_module

        with patch.object(providers, "MODULES", modules), patch.object(
            providers, "IDENTITY_RESOLUTION_PRIORITY", tuple()
        ):
            resolved = play_version_resolver.resolve_candidate(
                "com.example.app",
                VersionCandidate(name="9.8.7", raw="9.8.7 (3 patches)"),
            )

        self.assertEqual(resolved.code, "98742")
        self.assertEqual(resolved.name, "9.8.7")

    def test_raw_patch_code_is_verified_instead_of_trusted(self) -> None:
        def resolver(package, candidates):
            self.assertEqual(candidates[0].code, "111")
            return [VersionCandidate(name="4.5.6", code="45699")]

        fake_module = types.SimpleNamespace(resolve_candidate_identities=resolver)
        with patch.object(providers, "MODULES", {"metadata": fake_module}), patch.object(
            providers, "IDENTITY_RESOLUTION_PRIORITY", ("metadata",)
        ):
            resolved = play_version_resolver.resolve_candidate(
                "com.example.app",
                VersionCandidate(name="4.5.6", code="111", raw="111 (4.5.6)"),
            )
        self.assertEqual(resolved.code, "45699")

    def test_explicit_version_never_silently_becomes_current_release(self) -> None:
        with patch.object(providers, "MODULES", {}), patch.object(
            providers, "IDENTITY_RESOLUTION_PRIORITY", tuple()
        ):
            with self.assertRaises(play_version_resolver.VersionCodeResolutionError):
                play_version_resolver.resolve_candidate(
                    "com.example.app",
                    VersionCandidate(name="5.6.7", raw="5.6.7 (2 patches)"),
                )


class ApkPureIdentityResolverTests(unittest.TestCase):
    def setUp(self) -> None:
        versioning._DISCOVERED_VERSION_CODES.clear()

    def tearDown(self) -> None:
        versioning._DISCOVERED_VERSION_CODES.clear()

    def test_history_rows_accept_canonical_top_level_response(self) -> None:
        payload = {
            "version_list": [
                {
                    "package_name": "com.example.app",
                    "version_name": "7.8.9",
                    "version_code": 78942,
                }
            ]
        }
        rows = apkpure._history_rows_from_payload(payload)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["version_code"], 78942)

    def test_history_rows_accept_gateway_wrapped_response(self) -> None:
        payload = {
            "code": 0,
            "data": {
                "version_list": [
                    {
                        "package_name": "com.example.app",
                        "version_name": "7.8.9",
                        "version_code": 78942,
                    }
                ]
            },
        }
        rows = apkpure._history_rows_from_payload(payload)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["version_name"], "7.8.9")

    def test_history_rows_reject_unrelated_nested_lists(self) -> None:
        payload = {
            "data": {
                "items": [
                    {
                        "version_name": "7.8.9",
                        "version_code": 78942,
                    }
                ]
            }
        }
        self.assertEqual(apkpure._history_rows_from_payload(payload), [])

    def test_history_api_headers_do_not_inherit_web_referer(self) -> None:
        self.assertNotIn("Referer", apkpure.HISTORY_HEADERS)
        self.assertEqual(apkpure.HISTORY_HEADERS["Ual-Access-Businessid"], "projecta")

    def test_history_metadata_enriches_exact_version_name(self) -> None:
        rows = [
            {
                "package_name": "com.example.app",
                "version_name": "7.8.9",
                "version_code": 78942,
            },
            {
                "package_name": "com.example.app",
                "version_name": "7.8.8",
                "version_code": 78842,
            },
        ]
        with patch.object(apkpure, "_history_entries", return_value=rows):
            resolved = apkpure.resolve_candidate_identities(
                "com.example.app",
                [VersionCandidate(name="7.8.9", raw="7.8.9 (5 patches)")],
            )
        self.assertEqual(resolved[0].name, "7.8.9")
        self.assertEqual(resolved[0].code, "78942")
        self.assertEqual(
            versioning.discovered_version_code("com.example.app", "7.8.9"),
            "78942",
        )

    def test_history_metadata_rejects_different_package(self) -> None:
        rows = [
            {
                "package_name": "com.other.app",
                "version_name": "7.8.9",
                "version_code": 78942,
            }
        ]
        with patch.object(apkpure, "_history_entries", return_value=rows):
            requested = VersionCandidate(name="7.8.9", raw="7.8.9 (5 patches)")
            resolved = apkpure.resolve_candidate_identities(
                "com.example.app", [requested]
            )
        self.assertEqual(resolved, [requested])


if __name__ == "__main__":
    unittest.main()
