import unittest

from src.versioning import VersionCandidate, parse_candidate, parse_candidates


class VersioningTests(unittest.TestCase):
    def test_parses_poweramp_style_vendor_versions(self) -> None:
        candidate = parse_candidate("build-1025-bundle-play (1 patch)")
        self.assertIsNotNone(candidate)
        self.assertEqual(candidate.name, "build-1025-bundle-play")

    def test_parses_multiple_vendor_versions_from_cli_output(self) -> None:
        output = """INFO: Package name: com.maxmpz.audioplayer
Most common compatible versions:
\tbuild-1025-bundle-play (1 patch)
\tbuild-1025-uni (1 patch)
"""
        self.assertEqual(
            [candidate.name for candidate in parse_candidates(output)],
            ["build-1025-bundle-play", "build-1025-uni"],
        )

    def test_combined_vendor_version_matches_manifest_name_and_code(self) -> None:
        candidate = VersionCandidate(name="21.0.0.40")
        self.assertTrue(candidate.matches("21.0.0", "40"))
        self.assertFalse(candidate.matches("21.0.0", "41"))
        self.assertFalse(candidate.matches("21.0.1", "40"))

    def test_log_prose_is_not_parsed_as_vendor_version(self) -> None:
        self.assertIsNone(parse_candidate("build failed after 1025 attempts"))


if __name__ == "__main__":
    unittest.main()
