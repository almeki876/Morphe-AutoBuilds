import unittest
from unittest import mock

from scripts import verify_japan_egress


class VerifyJapanEgressTests(unittest.TestCase):
    def test_two_independent_jp_results_are_accepted(self) -> None:
        self.assertTrue(
            verify_japan_egress.is_verified_japan(
                {"cloudflare": "JP", "country.is": "JP"}
            )
        )

    def test_one_jp_result_is_not_enough(self) -> None:
        self.assertFalse(
            verify_japan_egress.is_verified_japan({"cloudflare": "JP"})
        )

    def test_conflicting_country_fails_closed(self) -> None:
        self.assertFalse(
            verify_japan_egress.is_verified_japan(
                {"cloudflare": "JP", "country.is": "US", "ipapi": "JP"}
            )
        )

    def test_main_retries_then_accepts_japan(self) -> None:
        with (
            mock.patch.object(
                verify_japan_egress,
                "probe_countries",
                side_effect=[
                    {"cloudflare": "US", "country.is": "US"},
                    {"cloudflare": "JP", "country.is": "JP"},
                ],
            ),
            mock.patch.object(verify_japan_egress.time, "sleep"),
        ):
            self.assertEqual(verify_japan_egress.main(), 0)

    def test_main_fails_without_japanese_egress(self) -> None:
        with (
            mock.patch.object(
                verify_japan_egress,
                "probe_countries",
                return_value={"cloudflare": "US", "country.is": "US"},
            ),
            mock.patch.object(verify_japan_egress.time, "sleep"),
        ):
            self.assertEqual(verify_japan_egress.main(), 1)


if __name__ == "__main__":
    unittest.main()
