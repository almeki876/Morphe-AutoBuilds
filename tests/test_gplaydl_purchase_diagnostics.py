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


if __name__ == "__main__":
    unittest.main()
