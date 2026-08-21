import unittest

from scripts.report_build_failure import _hypothesis


class FailureReportingTests(unittest.TestCase):
    def test_fingerprint_failure_lists_preparation_alternatives(self) -> None:
        message = _hypothesis(
            "SEVERE: FAILED: Proton VPN Premium\n"
            "PatchException: Failed to match the fingerprint",
            "Patch",
        )
        self.assertIn("wrong APK variant", message)
        self.assertIn("incomplete split bundle", message)
        self.assertNotIn("likely does not match", message)


if __name__ == "__main__":
    unittest.main()