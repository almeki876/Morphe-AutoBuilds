import json
import types
import unittest
from pathlib import Path
from unittest import mock

from src import providers
from src.versioning import VersionCandidate


class DynamicIdentityEnrichmentTests(unittest.TestCase):
    def test_repository_provider_configs_do_not_pin_version_codes(self) -> None:
        pinned = []
        for path in Path("apps").glob("*/*.json"):
            payload = json.loads(path.read_text(encoding="utf-8"))
            if "version_code" in payload:
                pinned.append(str(path))
        self.assertEqual(pinned, [])

    def test_all_opted_in_live_resolvers_are_used_without_priority_entry(self) -> None:
        requested = VersionCandidate(name="9.8.7", raw="9.8.7 (2 patches)")
        unresolved = types.SimpleNamespace(
            resolve_candidate_identities=lambda package, candidates: candidates
        )
        resolved = types.SimpleNamespace(
            resolve_candidate_identities=lambda package, candidates: [
                VersionCandidate(name="9.8.7", code="98742")
            ]
        )
        modules = {"first": unresolved, "future-provider": resolved}

        with (
            mock.patch.object(providers, "MODULES", modules),
            mock.patch.object(providers, "IDENTITY_RESOLUTION_PRIORITY", ("first",)),
        ):
            candidates = providers.resolve_patch_candidates(
                "example", "com.example.app", [requested]
            )

        self.assertEqual(
            candidates,
            [VersionCandidate(name="9.8.7", code="98742", raw=requested.raw)],
        )

    def test_identity_resolution_order_is_unique_and_live_only(self) -> None:
        resolver = types.SimpleNamespace(resolve_candidate_identities=lambda p, c: c)
        modules = {
            "metadata": resolver,
            "download-only": types.SimpleNamespace(),
            "fallback": resolver,
        }
        with (
            mock.patch.object(providers, "MODULES", modules),
            mock.patch.object(
                providers,
                "IDENTITY_RESOLUTION_PRIORITY",
                ("metadata", "download-only", "metadata"),
            ),
        ):
            self.assertEqual(
                providers.identity_resolution_order(),
                ("metadata", "fallback"),
            )

    def test_mismatched_live_identity_is_rejected(self) -> None:
        requested = VersionCandidate(name="9.8.7", raw="9.8.7 (2 patches)")
        wrong = types.SimpleNamespace(
            resolve_candidate_identities=lambda package, candidates: [
                VersionCandidate(name="9.8.8", code="98842")
            ]
        )
        with (
            mock.patch.object(providers, "MODULES", {"metadata": wrong}),
            mock.patch.object(providers, "IDENTITY_RESOLUTION_PRIORITY", ("metadata",)),
        ):
            candidates = providers.resolve_patch_candidates(
                "example", "com.example.app", [requested]
            )
        self.assertEqual(candidates, [requested])

    def test_unresolved_identity_does_not_fall_back_to_local_config(self) -> None:
        requested = VersionCandidate(name="9.8.7", raw="9.8.7 (2 patches)")
        with (
            mock.patch.object(providers, "MODULES", {}),
            mock.patch.object(providers, "IDENTITY_RESOLUTION_PRIORITY", ()),
            mock.patch("src.providers.load_config") as load_config,
        ):
            candidates = providers.resolve_patch_candidates(
                "example", "com.example.app", [requested]
            )
        self.assertEqual(candidates, [requested])
        load_config.assert_not_called()


if __name__ == "__main__":
    unittest.main()
