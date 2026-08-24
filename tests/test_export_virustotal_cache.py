import json
import tempfile
import unittest
from pathlib import Path

from scripts import export_virustotal_cache


class ExportVirusTotalCacheTests(unittest.TestCase):
    def test_exports_only_portable_cache(self):
        payload = {
            "title": "scan",
            "cache": {
                "version": 1,
                "results": {
                    "abc": {"verdict": "clean"},
                    "def": {"verdict": "clean"},
                },
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report = root / "report.json"
            output = root / "cache.json"
            report.write_text(json.dumps(payload), encoding="utf-8")

            count = export_virustotal_cache.export_cache(report, output)
            exported = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual(count, 2)
        self.assertEqual(exported, payload["cache"])

    def test_rejects_missing_results_map(self):
        with self.assertRaisesRegex(ValueError, "results map"):
            export_virustotal_cache.portable_cache({"cache": {"version": 1}})

    def test_rejects_unknown_cache_version(self):
        with self.assertRaisesRegex(ValueError, "v1 portable cache"):
            export_virustotal_cache.portable_cache(
                {"cache": {"version": 2, "results": {}}}
            )


if __name__ == "__main__":
    unittest.main()
