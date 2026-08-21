import unittest
from pathlib import Path


class WorkflowConfigurationTests(unittest.TestCase):
    def test_build_accepts_google_play_dispenser_secrets(self) -> None:
        workflow = Path(".github/workflows/build.yml").read_text(encoding="utf-8")

        self.assertIn(
            "GPLAY_DISPENSER_URLS: ${{ secrets.GPLAY_DISPENSER_URLS || vars.GPLAY_DISPENSER_URLS }}",
            workflow,
        )
        self.assertIn(
            "GPLAY_DISPENSER_URL: ${{ secrets.GPLAY_DISPENSER_URL || vars.GPLAY_DISPENSER_URL }}",
            workflow,
        )

    def test_open_issue_regression_set_is_targeted(self) -> None:
        workflow = Path(
            ".github/workflows/pr-targeted-build-verification.yml"
        ).read_text(encoding="utf-8")
        affected_apps = {
            "adobe-acrobat",
            "amazon-shopping",
            "crunchyroll",
            "fing",
            "ibs_paint",
            "lightroom",
            "nova",
            "twitch-android-tv",
            "youtube-music",
        }

        for app in affected_apps:
            with self.subTest(app=app):
                self.assertIn(app, workflow)


if __name__ == "__main__":
    unittest.main()
