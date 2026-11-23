import importlib.util
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_module(name: str, path: Path, stubs: dict[str, types.ModuleType]):
    with mock.patch.dict(sys.modules, stubs):
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        return module


def _load_utils():
    src_pkg = types.ModuleType("src")
    src_pkg.__path__ = []
    gh = types.ModuleType("src.gh")
    gh.get_repo = lambda name: None  # noqa: ARG005
    versioning = types.ModuleType("src.versioning")
    versioning.VersionCandidate = object
    versioning.parse_candidates = lambda *args, **kwargs: []
    cli_launch = types.ModuleType("src.cli_launch")
    console_output = types.ModuleType("src.console_output")
    src_pkg.gh = gh
    src_pkg.versioning = versioning
    src_pkg.cli_launch = cli_launch
    src_pkg.console_output = console_output
    return _load_module(
        "utils_under_test",
        REPO_ROOT / "src" / "utils.py",
        {
            "src": src_pkg,
            "src.gh": gh,
            "src.versioning": versioning,
            "src.cli_launch": cli_launch,
            "src.console_output": console_output,
        },
    )


class _Response:
    def __init__(self, headers: dict, url: str) -> None:
        self.headers = headers
        self.url = url


class ExtractFilenameSanitizationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.utils = _load_utils()

    def test_content_disposition_traversal_is_reduced_to_basename(self) -> None:
        response = _Response(
            {"content-disposition": 'attachment; filename="../../evil.apk"'},
            "https://example.com/download",
        )
        self.assertEqual(self.utils.extract_filename(response), "evil.apk")

    def test_content_disposition_absolute_path_is_reduced(self) -> None:
        response = _Response(
            {"content-disposition": 'attachment; filename="/tmp/evil.apk"'},
            "https://example.com/download",
        )
        self.assertEqual(self.utils.extract_filename(response), "evil.apk")

    def test_normal_filename_is_unchanged(self) -> None:
        response = _Response(
            {"content-disposition": 'attachment; filename="app release.apk"'},
            "https://example.com/download",
        )
        self.assertEqual(self.utils.extract_filename(response), "app release.apk")

    def test_redirect_content_disposition_traversal_is_reduced(self) -> None:
        response = _Response(
            {},
            "https://example.com/get?response-content-disposition="
            "attachment%3B%20filename%3D%22..%2F..%2Fevil.apk%22",
        )
        self.assertEqual(self.utils.extract_filename(response), "evil.apk")

    def test_fallback_url_basename(self) -> None:
        response = _Response({}, "https://example.com/files/app.apk?token=1")
        self.assertEqual(self.utils.extract_filename(response), "app.apk")


class DownloadResourceSanitizationTests(unittest.TestCase):
    def test_server_chosen_path_cannot_escape_the_working_directory(self) -> None:
        utils = _load_utils()

        apk_cache = types.ModuleType("src.apk_cache")
        apk_cache.is_valid_apk_archive = lambda *args, **kwargs: True
        provenance = types.ModuleType("src.provenance")
        providers = types.ModuleType("src.providers")
        downloads = types.ModuleType("src.downloads")
        downloads.normalize_download = lambda value: value
        versioning = types.ModuleType("src.versioning")
        versioning.VersionCandidate = object
        versioning.configured_fallback_candidates = lambda *a, **k: []
        versioning.pinned_candidate = lambda *a, **k: None

        src_pkg = types.ModuleType("src")
        src_pkg.__path__ = []
        src_pkg.utils = utils
        src_pkg.apk_cache = apk_cache
        src_pkg.provenance = provenance
        src_pkg.providers = providers
        src_pkg.downloads = downloads
        src_pkg.versioning = versioning

        downloader = _load_module(
            "downloader_under_test",
            REPO_ROOT / "src" / "downloader.py",
            {
                "src": src_pkg,
                "src.utils": utils,
                "src.apk_cache": apk_cache,
                "src.provenance": provenance,
                "src.providers": providers,
                "src.downloads": downloads,
                "src.versioning": versioning,
            },
        )

        class FakeResponse:
            headers = {
                "content-disposition": 'attachment; filename="../../evil.apk"',
                "content-length": "3",
            }
            url = "https://example.com/download"

            def raise_for_status(self) -> None:
                return None

            def iter_content(self, chunk_size: int):
                yield b"apk"

            def close(self) -> None:
                return None

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            work = root / "work"
            work.mkdir()
            original_cwd = Path.cwd()
            try:
                os.chdir(work)
                with mock.patch.object(
                    utils, "cf_aware_get", return_value=FakeResponse()
                ):
                    result = downloader.download_resource("https://example.com/x")
            finally:
                os.chdir(original_cwd)
            self.assertEqual(result.name, "evil.apk")
            self.assertTrue((work / "evil.apk").is_file())
            self.assertFalse((root / "evil.apk").exists())


if __name__ == "__main__":
    unittest.main()
