import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src import apk_identity, aurora_play
from src.versioning import VersionCandidate


class AuroraPlayCurrentProbeTests(unittest.TestCase):
    @mock.patch("src.aurora_play._download_with_linked_gplaydl")
    @mock.patch("src.aurora_play._download_with_apkeep_google_play")
    @mock.patch("src.aurora_play._download_with_playfetch_google_play")
    @mock.patch("src.aurora_play._download_with_fast_gplaydl")
    @mock.patch("src.aurora_play.apk_identity.validate_identity")
    def test_exact_failure_accepts_only_matching_current_fallback(
        self,
        validate_identity: mock.Mock,
        fast: mock.Mock,
        playfetch: mock.Mock,
        apkeep: mock.Mock,
        linked: mock.Mock,
    ) -> None:
        fast.side_effect = RuntimeError("requested version unavailable")
        playfetch.side_effect = RuntimeError("playfetch unavailable")
        candidate = VersionCandidate(name="14.0.6", code="1400060037")
        validate_identity.return_value = apk_identity.ApkIdentity(
            package_name="jp.ne.ibis.ibispaintx.app",
            version_name="14.0.6",
            version_code="1400060037",
        )

        with mock.patch.dict(os.environ, {"GPLAYDL_API_KEY": "secret-key"}, clear=False):
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                current = root / "apkeep-current.apk"
                current.write_bytes(b"base")
                apkeep.return_value = current
                result = aurora_play.download_candidate(
                    "jp.ne.ibis.ibispaintx.app",
                    candidate,
                    root,
                )

                self.assertIs(result, current)
                self.assertTrue(result.is_file())

        validate_identity.assert_called_once_with(
            result,
            "jp.ne.ibis.ibispaintx.app",
            candidate,
        )
        linked.assert_not_called()

    @mock.patch("src.aurora_play._download_with_linked_gplaydl")
    @mock.patch("src.aurora_play._download_with_apkeep_google_play")
    @mock.patch("src.aurora_play._download_with_playfetch_google_play")
    @mock.patch("src.aurora_play._download_with_fast_gplaydl")
    @mock.patch("src.aurora_play.apk_identity.validate_identity")
    def test_mismatched_current_probes_are_deleted_before_next_fallback(
        self,
        validate_identity: mock.Mock,
        fast: mock.Mock,
        playfetch: mock.Mock,
        apkeep: mock.Mock,
        linked: mock.Mock,
    ) -> None:
        fast.side_effect = RuntimeError("requested version unavailable")
        validate_identity.side_effect = apk_identity.ApkIdentityError(
            "APK version mismatch: current release differs"
        )
        linked.side_effect = RuntimeError("fresh profile also unavailable")
        candidate = VersionCandidate(name="32.13.2.100", code="1241320216")

        with mock.patch.dict(os.environ, {"GPLAYDL_API_KEY": "secret-key"}, clear=False):
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                playfetch_current = root / "playfetch-current.apk"
                apkeep_current = root / "apkeep-current.apk"
                playfetch_current.write_bytes(b"wrong-current-a")
                apkeep_current.write_bytes(b"wrong-current-b")
                playfetch.return_value = playfetch_current
                apkeep.return_value = apkeep_current

                with self.assertRaisesRegex(
                    RuntimeError, "all Google Play download paths failed"
                ):
                    aurora_play.download_candidate(
                        "com.amazon.mShop.android.shopping",
                        candidate,
                        root,
                    )

                self.assertFalse(playfetch_current.exists())
                self.assertFalse(apkeep_current.exists())

        self.assertEqual(validate_identity.call_count, 2)
        linked.assert_called_once_with(
            "com.amazon.mShop.android.shopping",
            candidate,
            mock.ANY,
        )


if __name__ == "__main__":
    unittest.main()
