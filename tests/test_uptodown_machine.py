from unittest.mock import patch

from bs4 import BeautifulSoup

from src import uptodown_machine
from src.versioning import VersionCandidate


def test_json_ld_exact_version_discovers_uptodown_url():
    soup = BeautifulSoup(
        """
        <html><head>
          <script type="application/ld+json">
            {
              "@type": "SoftwareApplication",
              "softwareVersion": "11.4.5",
              "downloadUrl": "https://dw.uptodown.com/dwn/exact-token"
            }
          </script>
        </head></html>
        """,
        "html.parser",
    )
    candidate = VersionCandidate(name="11.4.5")

    assert uptodown_machine._structured_urls(
        soup,
        "https://lightroom-photo-editor.en.uptodown.com/android/versions",
        candidate,
    ) == ["https://dw.uptodown.com/dwn/exact-token"]


def test_json_ld_does_not_match_neighbouring_version():
    soup = BeautifulSoup(
        """
        <script type="application/ld+json">
          {
            "softwareVersion": "11.4.50",
            "downloadUrl": "https://dw.uptodown.com/dwn/wrong-token"
          }
        </script>
        """,
        "html.parser",
    )

    assert uptodown_machine._structured_urls(
        soup,
        "https://lightroom-photo-editor.en.uptodown.com/android/versions",
        VersionCandidate(name="11.4.5"),
    ) == []


def test_advertised_atom_feed_discovers_exact_release_link():
    feed = b"""<?xml version="1.0" encoding="utf-8"?>
    <feed xmlns="http://www.w3.org/2005/Atom">
      <entry>
        <title>Lightroom 11.4.5 APK</title>
        <link href="/android/download/12345" />
      </entry>
      <entry>
        <title>Lightroom 11.4.6 APK</title>
        <link href="/android/download/67890" />
      </entry>
    </feed>
    """

    assert uptodown_machine._feed_candidate_urls(
        feed,
        "https://lightroom-photo-editor.en.uptodown.com/android/feed.xml",
        VersionCandidate(name="11.4.5"),
    ) == [
        "https://lightroom-photo-editor.en.uptodown.com/android/download/12345"
    ]


def test_page_feed_discovery_rejects_foreign_hosts():
    soup = BeautifulSoup(
        """
        <head>
          <link rel="alternate" type="application/atom+xml"
                href="/android/feed.xml" />
          <link rel="alternate" type="application/rss+xml"
                href="https://evil.example/feed.xml" />
        </head>
        """,
        "html.parser",
    )

    assert uptodown_machine._feed_urls(
        soup,
        "https://crunchyroll.en.uptodown.com/android/versions",
    ) == ["https://crunchyroll.en.uptodown.com/android/feed.xml"]


def test_provider_uses_structured_discovery_only_after_existing_routes_fail():
    candidate = VersionCandidate(name="3.112.2")
    config = {"name": "crunchyroll", "package": "com.crunchyroll.crunchyroid"}
    direct = "https://dw.uptodown.com/dwn/exact-token"

    with patch.object(
        uptodown_machine.base,
        "get_download_link_for_candidate",
        return_value=None,
    ) as existing, patch.object(
        uptodown_machine,
        "_structured_download_link",
        return_value=direct,
    ) as structured:
        result = uptodown_machine.get_download_link_for_candidate(
            candidate,
            "crunchyroll",
            config,
        )

    assert result == direct
    existing.assert_called_once_with(candidate, "crunchyroll", config)
    structured.assert_called_once_with(candidate, "crunchyroll", config)


def test_provider_does_not_add_feed_requests_when_existing_route_succeeds():
    candidate = VersionCandidate(name="3.112.2")
    config = {"name": "crunchyroll", "package": "com.crunchyroll.crunchyroid"}
    direct = "https://dw.uptodown.com/dwn/existing-token"

    with patch.object(
        uptodown_machine.base,
        "get_download_link_for_candidate",
        return_value=direct,
    ), patch.object(uptodown_machine, "_structured_download_link") as structured:
        result = uptodown_machine.get_download_link_for_candidate(
            candidate,
            "crunchyroll",
            config,
        )

    assert result == direct
    structured.assert_not_called()
