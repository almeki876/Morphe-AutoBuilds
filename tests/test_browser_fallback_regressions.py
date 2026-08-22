from __future__ import annotations

import zipfile
from types import SimpleNamespace

import pytest

from src import apk_validation, browser_fallback, downloader


def test_uptodown_generic_current_download_is_not_historical_proof():
    assert browser_fallback._is_generic_uptodown_url(
        "https://example.en.uptodown.com/android/download"
    )
    assert not browser_fallback._is_generic_uptodown_url(
        "https://example.en.uptodown.com/android/download/123456789"
    )
    assert not browser_fallback._is_generic_uptodown_url(
        "https://dw.uptodown.com/dwn/release-token"
    )


def test_bundle_normalizer_rejects_unrelated_zip(tmp_path):
    archive = tmp_path / "not-an-apk.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("readme.txt", "not an APK bundle")

    with pytest.raises(browser_fallback.BrowserFallbackError, match="no APK modules"):
        browser_fallback._normalize_apk_bundle(archive)


def test_bundle_normalizer_merges_split_bundle_before_manifest_validation(
    tmp_path, monkeypatch
):
    archive = tmp_path / "release.xapk"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("base.apk", b"base")
        bundle.writestr("config.arm64_v8a.apk", b"split")

    editor = tmp_path / "APKEditor.jar"
    editor.write_bytes(b"jar")
    monkeypatch.setattr(downloader, "download_apkeditor", lambda: editor)

    validated = []

    def fake_validate(path):
        validated.append(path)
        if path == archive:
            raise apk_validation.ApkValidationError("root manifest missing")

    monkeypatch.setattr(apk_validation, "assert_valid_apk_archive", fake_validate)

    commands = []

    def fake_run(command, **kwargs):
        commands.append(command)
        output = archive.with_name("release-merged.apk")
        output.write_bytes(b"merged")
        return SimpleNamespace(returncode=0, stdout="merged")

    monkeypatch.setattr(browser_fallback.subprocess, "run", fake_run)

    merged = browser_fallback._normalize_apk_bundle(archive)

    assert merged.name == "release-merged.apk"
    assert merged.exists()
    assert not archive.exists()
    assert validated[-1] == merged
    assert commands
    assert commands[0][:4] == ["java", "-jar", str(editor), "m"]
    assert "-validate-modules" in commands[0]
