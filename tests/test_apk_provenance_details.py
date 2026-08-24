import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src import apk_cache, provenance


class ApkProvenanceDetailsTests(unittest.TestCase):
    def tearDown(self) -> None:
        provenance._PENDING_ENTRIES.clear()

    def test_cache_restore_recovers_original_provider_from_sidecar(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache_dir = root / "cache"
            cache_dir.mkdir()
            package = "com.example.app"
            version = "1.2.3"
            asset_name = (
                apk_cache._asset_prefix(package, version)
                + ("0" * 64)
                + ".apk"
            )
            sidecar = cache_dir / f"{asset_name}.origin.json"
            sidecar.write_text(
                json.dumps(
                    {
                        "provider": "apkmirror",
                        "provider_label": "APKMirror",
                        "provider_url": "https://www.apkmirror.com/",
                        "origin_url": "https://www.apkmirror.com/example/",
                    }
                ),
                encoding="utf-8",
            )

            metadata = root / "build-metadata/apk-sources.json"
            shared = root / "base-apk-input/origin.json"
            fake_apk = root / "cached.apk"
            payload_bytes = b"cache payload"
            fake_apk.write_bytes(payload_bytes)

            with (
                mock.patch.object(apk_cache, "CACHE_DIR", cache_dir),
                mock.patch.object(provenance, "METADATA_PATH", metadata),
                mock.patch.object(provenance, "SHARED_ORIGIN_PATH", shared),
                mock.patch.dict("os.environ", {"SOURCE": "source"}, clear=False),
            ):
                provenance.record(
                    "example",
                    version,
                    "cache",
                    fake_apk,
                    "arm64-v8a",
                    config={"package": package},
                )

            payload = json.loads(shared.read_text(encoding="utf-8"))
            self.assertTrue(payload["cached"])
            self.assertEqual(payload["retrieval_method"], "cache_restore")
            self.assertTrue(payload["cache_origin_sidecar_found"])
            self.assertEqual(payload["provider"], "apkmirror")
            self.assertEqual(payload["provider_label"], "APKMirror")
            self.assertEqual(
                payload["origin_url"],
                "https://www.apkmirror.com/example/",
            )
            self.assertEqual(
                payload["sha256"], hashlib.sha256(payload_bytes).hexdigest()
            )
            self.assertFalse(payload["legacy_cache_origin_unknown"])

    def test_legacy_cache_is_explicitly_marked_unknown(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache_dir = root / "cache"
            cache_dir.mkdir()
            metadata = root / "build-metadata/apk-sources.json"
            shared = root / "base-apk-input/origin.json"
            fake_apk = root / "cached.apk"
            fake_apk.write_bytes(b"legacy")

            with (
                mock.patch.object(apk_cache, "CACHE_DIR", cache_dir),
                mock.patch.object(provenance, "METADATA_PATH", metadata),
                mock.patch.object(provenance, "SHARED_ORIGIN_PATH", shared),
                mock.patch.dict("os.environ", {"SOURCE": "source"}, clear=False),
            ):
                provenance.record(
                    "example",
                    "1.0",
                    "cache",
                    fake_apk,
                    "universal",
                    config={"package": "com.example.app"},
                )

            payload = json.loads(shared.read_text(encoding="utf-8"))
            self.assertTrue(payload["cached"])
            self.assertFalse(payload["cache_origin_sidecar_found"])
            self.assertTrue(payload["legacy_cache_origin_unknown"])

    def test_google_play_origin_url_is_package_specific(self) -> None:
        url = provenance._origin_url(
            "aurora-google-play",
            {"package": "jp.example.bank"},
            "",
        )
        self.assertEqual(
            url,
            "https://play.google.com/store/apps/details?id=jp.example.bank",
        )


if __name__ == "__main__":
    unittest.main()
