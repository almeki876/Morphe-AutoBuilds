import importlib.util
import os
import unittest
from pathlib import Path
from unittest import mock


RUNNER_PATH = Path(__file__).resolve().parents[1] / "tools" / "gplaydl_market_runner.py"
SPEC = importlib.util.spec_from_file_location("gplaydl_market_runner", RUNNER_PATH)
assert SPEC and SPEC.loader
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


def _len_field(field_number: int, payload: bytes) -> bytes:
    tag = (field_number << 3) | 2
    encoded_tag = bytearray()
    while True:
        part = tag & 0x7F
        tag >>= 7
        if tag:
            encoded_tag.append(part | 0x80)
        else:
            encoded_tag.append(part)
            break
    if len(payload) >= 128:
        raise ValueError("test helper only supports short payloads")
    return bytes(encoded_tag) + bytes([len(payload)]) + payload


class GPlayDlMarketRunnerTests(unittest.TestCase):
    def test_toc_cookie_decodes_current_dispenser_proto_path(self) -> None:
        cookie = b"market-cookie"
        toc = _len_field(22, cookie)
        payload = _len_field(6, toc)
        wrapper = _len_field(1, payload)

        self.assertEqual(runner._toc_cookie(wrapper), "market-cookie")

    def test_invalid_mccmnc_is_rejected(self) -> None:
        with mock.patch.dict(
            os.environ,
            {"GPLAYDL_MARKET_MCCMNC": "not-a-market"},
            clear=False,
        ):
            with self.assertRaisesRegex(ValueError, "5- or 6-digit"):
                runner._configured_mccmnc()

    def test_market_configuration_is_app_agnostic(self) -> None:
        with mock.patch.dict(
            os.environ,
            {
                "GPLAYDL_MARKET_LOCALE": "ja_JP",
                "GPLAYDL_MARKET_MCCMNC": "44010",
            },
            clear=False,
        ):
            self.assertEqual(runner._configured_locale(), "ja_JP")
            self.assertEqual(runner._configured_mccmnc(), "44010")


if __name__ == "__main__":
    unittest.main()
