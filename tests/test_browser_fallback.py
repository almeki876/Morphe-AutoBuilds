from pathlib import Path
from unittest.mock import patch

from src import browser_fallback
from src.versioning import VersionCandidate


def test_challenge_detection_rejects_interactive_pages():
    assert browser_fallback._challenge_present(
        "Just a moment...",
        "<html><script src='/cdn-cgi/challenge-platform/x'></script></html>",
    )
    assert not browser_fallback._challenge_present(
        "Older versions",
        "<html><body>11.4.5</body></html>",
    )


def test_signed_uptodown_token_is_converted_to_redacted_cdn_path():
    url = browser_fallback._safe_download_url(
        "abcdefghijklmnopqrstuvwxyz0123456789",
        "https://example.en.uptodown.com/android/download",
    )
    assert url == "https://dw.uptodown.com/dwn/abcdefghijklmnopqrstuvwxyz0123456789"


def test_browser_discovery_still_uses_hardened_apk_downloader(tmp_path):
    spec = browser_fallback.BrowserDownload(
        url="https://dw.uptodown.com/dwn/token",
        headers={"Referer": "https://example.en.uptodown.com/android/download"},
        source="browser-uptodown",
    )
    downloaded = tmp_path / "input.xapk"
    downloaded.write_bytes(b"PK\x03\x04")

    with patch.object(browser_fallback, "resolve_uptodown_download", return_value=spec), patch(
        "src.downloader.download_resource", return_value=downloaded
    ) as download_resource:
        result = browser_fallback.download_candidate(
            "lightroom",
            "com.adobe.lrmobile",
            VersionCandidate(name="11.4.5"),
        )

    assert result == downloaded
    download_resource.assert_called_once_with(
        spec.url,
        headers=spec.headers,
        validate_apk=True,
    )
