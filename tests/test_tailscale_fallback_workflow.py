import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = (ROOT / ".github/workflows/build.yml").read_text(encoding="utf-8")


def _step(step_id: str) -> str:
    marker = f"\n        id: {step_id}\n"
    marker_index = WORKFLOW.index(marker)
    start = WORKFLOW.rindex("\n      - name:", 0, marker_index)
    end = WORKFLOW.find("\n      - name:", marker_index)
    return WORKFLOW[start : end if end >= 0 else None]


def _named_step(name: str) -> str:
    marker = f"\n      - name: {name}\n"
    start = WORKFLOW.index(marker)
    end = WORKFLOW.find("\n      - name:", start + len(marker))
    return WORKFLOW[start : end if end >= 0 else None]


class TailscaleFallbackWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.primary = _step("download-apk-primary")
        cls.fallback = _step("tailscale-fallback")
        cls.retry = _step("download-apk-japan")
        cls.result = _step("download-apk")

    def test_primary_download_is_attempted_before_tailscale(self) -> None:
        self.assertLess(
            WORKFLOW.index("id: download-apk-primary"),
            WORKFLOW.index("id: tailscale-fallback"),
        )
        self.assertIn("continue-on-error: true", self.primary)

    def test_tailscale_fallback_is_not_limited_to_named_apps(self) -> None:
        self.assertIn(
            "if: ${{ steps.download-apk-primary.outcome == 'failure' }}",
            self.fallback,
        )
        self.assertNotIn("matrix.app_name", self.fallback)

    def test_retry_requires_verified_japanese_egress(self) -> None:
        self.assertIn(
            "if: ${{ steps.verify-japan-egress.outcome == 'success' }}",
            self.retry,
        )
        self.assertIn("continue-on-error: true", self.retry)

    def test_both_download_attempts_receive_exact_identity_metadata_key(self) -> None:
        secret = "VIRUSTOTAL_API_KEY: ${{ secrets.VIRUSTOTAL_API_KEY }}"
        self.assertIn(secret, self.primary)
        self.assertIn(secret, self.retry)

    def test_final_result_accepts_primary_or_japan_success(self) -> None:
        self.assertIn("if: ${{ always() }}", self.result)
        self.assertIn(
            "PRIMARY_OUTCOME: ${{ steps.download-apk-primary.outcome }}",
            self.result,
        )
        self.assertIn(
            "JAPAN_OUTCOME: ${{ steps.download-apk-japan.outcome }}",
            self.result,
        )

    def test_verified_cache_candidate_reaches_successful_build_gate(self) -> None:
        base_input = _named_step("Upload Base APK Input")
        cache_upload = _named_step("Upload Verified Base APK Cache Candidate")

        self.assertIn("if: success()", base_input)
        self.assertIn("base-apk-cache-out/*", base_input)
        self.assertIn("if: success()", cache_upload)
        self.assertIn("base-apk-cache-out/*", cache_upload)


if __name__ == "__main__":
    unittest.main()
