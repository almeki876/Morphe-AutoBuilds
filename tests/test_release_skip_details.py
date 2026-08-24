import unittest

from scripts.append_release_skip_details import collect_runtime_skips


class ReleaseSkipDetailsTests(unittest.TestCase):
    def test_extracts_default_disabled_and_unsupported_for_exact_job(self) -> None:
        log = "\n".join(
            [
                "Build youtube-music with Anddea\tBuild APK\t2026-08-24T05:00:00Z INFO: Skipping disabled: Spoof signature (default)",
                'Build youtube-music with Anddea\tBuild APK\t2026-08-24T05:00:01Z WARNING: "Enable compact dialog" is not supported in this version. Use YouTube Music 8.30.54 or earlier.',
                "Build youtube with Anddea\tBuild APK\t2026-08-24T05:00:02Z INFO: Skipping disabled: Change installer package name (default)",
            ]
        )
        result = collect_runtime_skips(
            log,
            app="youtube-music",
            source_label="Anddea",
        )
        self.assertEqual(
            result,
            [
                {
                    "name": "Spoof signature",
                    "category": "default-disabled",
                    "reason": "CLI: Skipping disabled (default)",
                },
                {
                    "name": "Enable compact dialog",
                    "category": "unsupported",
                    "reason": "CLI: not supported in this APK version. Use YouTube Music 8.30.54 or earlier.",
                },
            ],
        )

    def test_deduplicates_repeated_cli_observations(self) -> None:
        line = "Build youtube with Anddea\tBuild APK\tINFO: Skipping disabled: Spoof signature (default)"
        result = collect_runtime_skips(
            f"{line}\n{line}\n",
            app="youtube",
            source_label="Anddea",
        )
        self.assertEqual(len(result), 1)


if __name__ == "__main__":
    unittest.main()
