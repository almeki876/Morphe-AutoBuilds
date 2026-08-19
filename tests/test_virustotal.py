from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.virustotal import VirusTotalClient, VirusTotalError


class FakeResponse:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload
        self.headers: dict[str, str] = {}
        self.text = ""

    def json(self) -> dict:
        return self._payload


class FakeClient(VirusTotalClient):
    def __init__(self, responses: list[FakeResponse], **kwargs):
        kwargs.setdefault("analysis_timeout", 120)
        super().__init__(
            "test-key", request_interval=0, poll_interval=0, max_retries=0, **kwargs
        )
        self.responses = iter(responses)
        self.calls: list[tuple[str, str]] = []

    def request(self, method: str, url: str, **kwargs):
        self.calls.append((method, url))
        return next(self.responses)


def report(last_analysis_date: int) -> FakeResponse:
    return FakeResponse(
        200,
        {
            "data": {
                "attributes": {
                    "last_analysis_date": last_analysis_date,
                    "last_analysis_stats": {"malicious": 1, "undetected": 10},
                    "last_analysis_results": {
                        "MaxSecure": {"category": "malicious", "result": "old"}
                    },
                }
            }
        },
    )


def clean_report(last_analysis_date: int | None) -> FakeResponse:
    attributes = {
        "last_analysis_stats": {"malicious": 0, "suspicious": 0, "undetected": 11},
        "last_analysis_results": {},
    }
    if last_analysis_date is not None:
        attributes["last_analysis_date"] = last_analysis_date
    return FakeResponse(200, {"data": {"attributes": attributes}})


class VirusTotalTests(unittest.TestCase):
    def test_stale_hash_queues_reanalysis_and_uses_completed_results(self):
        old_date = int(time.time()) - 91 * 86400
        client = FakeClient(
            [
                report(old_date),
                FakeResponse(200, {"data": {"id": "analysis-1"}}),
                FakeResponse(
                    200,
                    {
                        "data": {
                            "attributes": {
                                "status": "completed",
                                "date": int(time.time()),
                                "stats": {"malicious": 0, "undetected": 11},
                                "results": {},
                            }
                        }
                    },
                ),
            ],
            max_analysis_age_days=90,
        )

        with tempfile.NamedTemporaryFile(suffix=".apk") as stream:
            analysis_id, stats, engines, method, date, reanalyzed = client.start_analysis(
                Path(stream.name), "a" * 64
            )
            completed_stats, completed_engines, completed_date = client.wait_for_analysis(
                analysis_id or "", "test.apk"
            )

        self.assertEqual(analysis_id, "analysis-1")
        self.assertEqual(stats["malicious"], 1)
        self.assertEqual(engines["MaxSecure"]["category"], "malicious")
        self.assertEqual(method, "reanalyzed")
        self.assertEqual(date, old_date)
        self.assertTrue(reanalyzed)
        self.assertEqual(completed_stats["malicious"], 0)
        self.assertEqual(completed_engines, {})
        self.assertIsInstance(completed_date, int)
        self.assertEqual([call[0] for call in client.calls], ["GET", "POST", "GET"])

    def test_stale_clean_hash_is_not_reanalyzed(self):
        client = FakeClient(
            [clean_report(int(time.time()) - 91 * 86400)],
            max_analysis_age_days=90,
        )

        with tempfile.NamedTemporaryFile(suffix=".apk") as stream:
            analysis_id, stats, engines, method, date, reanalyzed = client.start_analysis(
                Path(stream.name), "c" * 64
            )

        self.assertIsNone(analysis_id)
        self.assertEqual(stats["malicious"], 0)
        self.assertEqual(engines, {})
        self.assertEqual(method, "hash lookup")
        self.assertFalse(reanalyzed)
        self.assertEqual(date, int(time.time()) - 91 * 86400)
        self.assertEqual([call[0] for call in client.calls], ["GET"])

    def test_timeout_can_fall_back_to_old_result(self):
        client = FakeClient(
            [
                report(int(time.time()) - 91 * 86400),
                FakeResponse(200, {"data": {"id": "analysis-2"}}),
            ],
            max_analysis_age_days=90,
            analysis_timeout=0,
        )
        with tempfile.NamedTemporaryFile(suffix=".apk") as stream:
            with patch("scripts.virustotal.sha256_file", return_value="b" * 64):
                result = client.scan(Path(stream.name))

        self.assertEqual(result.method, "stale fallback")
        self.assertFalse(result.reanalyzed)
        self.assertEqual(result.malicious, 1)
        self.assertEqual(result.verdict, "clean")


if __name__ == "__main__":
    unittest.main()