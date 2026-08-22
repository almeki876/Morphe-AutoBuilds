import types
import unittest
from unittest.mock import patch

from src import play_version_resolver, providers, versioning
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

    def test_explicit_version_never_silently_becomes_current_release(self) -> None:
        with patch.object(providers, "MODULES", {}), patch.object(
            providers, "IDENTITY_RESOLUTION_PRIORITY", tuple()
        ):
            with self.assertRaises(play_version_resolver.VersionCodeResolutionError):
                play_version_resolver.resolve_candidate(
                    "com.example.app",
                    VersionCandidate(name="5.6.7", raw="5.6.7 (2 patches)"),
                )


if __name__ == "__main__":
    unittest.main()
