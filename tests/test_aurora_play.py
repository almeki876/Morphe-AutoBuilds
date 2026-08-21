import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src import aurora_play


class _Response:
    def __init__(self, body: bytes):
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class AuroraPlayTests(unittest.TestCase):
    @mock.patch("src.aurora_play._read_url")
    def test_device_properties_reads_named_profile(self, read_url: mock.Mock) -> None:
        read_url.return_value = (
            b"[other]\nBuild.MODEL=Other\n"
            b"[px_9a]\n"
            b"Build.MODEL=Pixel 9a\n"
            b"Build.FINGERPRINT=google/tegu/tegu\\:16/ABC/123\\:user/release-keys\n"
            b"Platforms=arm64-v8a,armeabi-v7a\n"
        )

        properties = aurora_play._device_properties("px_9a")

        self.assertEqual(properties["Build.MODEL"], "Pixel 9a")
        self.assertEqual(
            properties["Build.FINGERPRINT"],
            "google/tegu/tegu:16/ABC/123:user/release-keys",
        )

    @mock.patch("src.aurora_play._device_properties", return_value={"Build.MODEL": "Pixel", "Build.FINGERPRINT": "fp"})
    @mock.patch("src.aurora_play.urlopen")
    def test_anonymous_auth_reads_current_aurora_schema(
        self,
        urlopen: mock.Mock,
        _device_properties: mock.Mock,
    ) -> None:
        urlopen.return_value = _Response(
            b'{"email":"anonymous@example.com","authToken":"ya29.secret"}'
        )

        email, token = aurora_play._anonymous_auth(device="px_9a")

        self.assertEqual(email, "anonymous@example.com")
        self.assertEqual(token, "ya29.secret")
        request = urlopen.call_args.args[0]
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.get_header("Content-type"), "application/json")

    @mock.patch("src.aurora_play._anonymous_auth", return_value=("anon@example.com", "ya29.secret"))
    @mock.patch("src.aurora_play._find_apkeep", return_value="apkeep")
    @mock.patch("src.aurora_play.subprocess.run")
    def test_download_current_uses_google_play_without_version_guess(
        self,
        run: mock.Mock,
        _find_apkeep: mock.Mock,
        _anonymous_auth: mock.Mock,
    ) -> None:
        def fake_run(command, **kwargs):
            output = Path(command[-1])
            (output / "base.apk").write_bytes(b"apk")
            return mock.Mock(returncode=0, stdout="ok")

        run.side_effect = fake_run
        with tempfile.TemporaryDirectory() as directory:
            path = aurora_play.download_current(
                "com.amazon.mShop.android.shopping",
                Path(directory),
                device="px_9a",
            )
            self.assertEqual(path.read_bytes(), b"apk")

        command = run.call_args.args[0]
        self.assertIn("google-play", command)
        self.assertIn("--auth-token", command)
        self.assertNotIn("com.amazon.mShop.android.shopping@32.13.2.100", command)


if __name__ == "__main__":
    unittest.main()
