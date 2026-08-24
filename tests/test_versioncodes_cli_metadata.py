import unittest

from src.versioning import parse_candidate


class VersionCodesCliMetadataTests(unittest.TestCase):
    def test_strips_trailing_arch_version_codes_metadata(self):
        raw = (
            "16.11.1(262250408)(9a6c828835) "
            "[versionCodes: ARM64_V8A=262250408, ARMEABI_V7A=262250408, "
            "X86_64=262250408, X86=262250408]"
        )

        candidate = parse_candidate(raw)

        self.assertIsNotNone(candidate)
        assert candidate is not None
        self.assertEqual(candidate.name, "16.11.1(262250408)(9a6c828835)")
        self.assertIsNone(candidate.code)
        self.assertTrue(
            candidate.matches(
                "16.11.1(262250408)(9a6c828835)",
                "262250408",
            )
        )

    def test_does_not_strip_unrecognized_bracket_suffix(self):
        raw = "16.11.1(262250408)(9a6c828835) [otherMetadata: 262250408]"

        candidate = parse_candidate(raw)

        self.assertIsNotNone(candidate)
        assert candidate is not None
        self.assertEqual(candidate.name, raw)
        self.assertFalse(
            candidate.matches(
                "16.11.1(262250408)(9a6c828835)",
                "262250408",
            )
        )


if __name__ == "__main__":
    unittest.main()
