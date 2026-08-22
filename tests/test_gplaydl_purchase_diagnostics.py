import unittest

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


if __name__ == "__main__":
    unittest.main()
