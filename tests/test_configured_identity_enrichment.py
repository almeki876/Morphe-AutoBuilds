import unittest
from unittest import mock

from src import providers
from src.versioning import VersionCandidate


class ConfiguredIdentityEnrichmentTests(unittest.TestCase):
    def test_exact_config_version_adds_missing_version_code(self) -> None:
        candidate = VersionCandidate(name="26.32.1")
        with (
            mock.patch.object(providers, "IDENTITY_RESOLUTION_PRIORITY", ()),
            mock.patch(
                "src.providers.load_config",
                side_effect=lambda app, provider, allow_synthetic=False: (
                    {
                        "name": "alarmy-alarm-clock-sleep-tracker",
                        "package": "droom.sleepIfUCan",
                        "version": "26.32.1",
                        "version_code": "263201",
                    }
                    if provider == "apkpure"
                    else None
                ),
            ),
        ):
            resolved = providers.resolve_patch_candidates(
                "alarmy", "droom.sleepIfUCan", [candidate]
            )

        self.assertEqual(resolved[0].name, "26.32.1")
        self.assertEqual(resolved[0].code, "263201")
        self.assertTrue(resolved[0].matches("", "263201"))

    def test_verified_config_replaces_cli_display_id_for_exact_version(self) -> None:
        candidate = VersionCandidate(
            name="16.0.20326.20034",
            code="20326",
            raw="20326 (16.0.20326.20034) (1 patch)",
        )
        with (
            mock.patch.object(providers, "IDENTITY_RESOLUTION_PRIORITY", ()),
            mock.patch(
                "src.providers.load_config",
                side_effect=lambda app, provider, allow_synthetic=False: (
                    {
                        "name": "microsoft-word",
                        "package": "com.microsoft.office.word",
                        "version": "16.0.20326.20034",
                        "version_code": "2005292331",
                    }
                    if provider == "apkmirror"
                    else None
                ),
            ),
        ):
            resolved = providers.resolve_patch_candidates(
                "word", "com.microsoft.office.word", [candidate]
            )

        self.assertEqual(resolved[0].name, "16.0.20326.20034")
        self.assertEqual(resolved[0].code, "2005292331")
        self.assertEqual(resolved[0].raw, candidate.raw)

    def test_stale_config_version_cannot_enrich_new_patch_version(self) -> None:
        candidate = VersionCandidate(name="26.33.1")
        with (
            mock.patch.object(providers, "IDENTITY_RESOLUTION_PRIORITY", ()),
            mock.patch(
                "src.providers.load_config",
                side_effect=lambda app, provider, allow_synthetic=False: (
                    {
                        "name": "alarmy-alarm-clock-sleep-tracker",
                        "package": "droom.sleepIfUCan",
                        "version": "26.32.1",
                        "version_code": "263201",
                    }
                    if provider == "apkpure"
                    else None
                ),
            ),
        ):
            resolved = providers.resolve_patch_candidates(
                "alarmy", "droom.sleepIfUCan", [candidate]
            )

        self.assertEqual(resolved, [candidate])
        self.assertIsNone(resolved[0].code)

    def test_existing_explicit_or_live_code_is_never_overwritten(self) -> None:
        candidate = VersionCandidate(name="26.32.1", code="999999")
        with (
            mock.patch.object(providers, "IDENTITY_RESOLUTION_PRIORITY", ()),
            mock.patch(
                "src.providers.load_config",
                return_value={
                    "package": "droom.sleepIfUCan",
                    "version": "26.32.1",
                    "version_code": "263201",
                },
            ),
        ):
            resolved = providers.resolve_patch_candidates(
                "alarmy", "droom.sleepIfUCan", [candidate]
            )

        self.assertEqual(resolved[0].code, "999999")


if __name__ == "__main__":
    unittest.main()
