import os
import unittest
from unittest import mock

from scripts import diagnose_gplay_purchase


class DiagnoseGPlayPurchaseScriptTests(unittest.TestCase):
    @mock.patch.object(
        diagnose_gplay_purchase.local_gplaydl_dispenser,
        "ensure_running",
        return_value=True,
    )
    @mock.patch.object(
        diagnose_gplay_purchase.gplaydl_purchase_diagnostics,
        "diagnose_priority_profiles",
    )
    @mock.patch.object(
        diagnose_gplay_purchase.gplaydl_purchase_diagnostics,
        "diagnose_purchase_failure",
    )
    def test_comma_separated_packages_run_baseline_and_profile_matrix(
        self,
        baseline: mock.Mock,
        profiles: mock.Mock,
        _ensure_running: mock.Mock,
    ) -> None:
        with mock.patch.dict(
            os.environ,
            {
                "GPLAY_DIAGNOSTIC_PACKAGE": "jp.example.first, jp.example.second",
                "GPLAY_DIAGNOSTIC_VERSION_CODE": "",
            },
            clear=False,
        ):
            self.assertEqual(diagnose_gplay_purchase.main(), 0)

        self.assertEqual(
            baseline.call_args_list,
            [
                mock.call("jp.example.first", None),
                mock.call("jp.example.second", None),
            ],
        )
        self.assertEqual(
            profiles.call_args_list,
            [
                mock.call("jp.example.first", None),
                mock.call("jp.example.second", None),
            ],
        )

    def test_exact_version_is_rejected_for_multiple_packages(self) -> None:
        with mock.patch.dict(
            os.environ,
            {
                "GPLAY_DIAGNOSTIC_PACKAGE": "jp.example.first,jp.example.second",
                "GPLAY_DIAGNOSTIC_VERSION_CODE": "40",
            },
            clear=False,
        ):
            with self.assertRaisesRegex(RuntimeError, "only be used with one"):
                diagnose_gplay_purchase.main()


if __name__ == "__main__":
    unittest.main()
