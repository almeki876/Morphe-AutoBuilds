import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from src import cli_launch


def _zip(path: Path, entries: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    return path


class CliLaunchTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        patcher = mock.patch.dict(os.environ, {"CLI_SHIM_DIR": str(self.root / "shims")})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.cli = _zip(
            self.root / "morphe-desktop-2.0.0-all.jar",
            {
                "META-INF/MANIFEST.MF": "Manifest-Version: 1.0\r\nMain-Class: app.Main\r\n\r\n",
                "org/slf4j/helpers/NamedLoggerBase.class": b"cli",
            },
        )

    def _command(self, bundle: Path) -> list[str]:
        return ["java", "-Xmx1g", "-jar", str(self.cli), "list-versions",
                "--patches", str(bundle), "-f", "pkg"]

    def test_bundle_with_missing_slf4j_classes_gets_classpath_shim(self) -> None:
        bundle = _zip(self.root / "patches-1.mpp", {
            "org/slf4j/helpers/MarkerIgnoringBase.class": b"bundle",
            "org/slf4j/helpers/NamedLoggerBase.class": b"dup",
            "patch/Foo.class": b"x",
        })
        result = cli_launch.prepare_command(self._command(bundle))
        self.assertEqual(result[:4], ["java", "-Xmx1g", "-cp", result[3]])
        self.assertNotIn("-jar", result)
        self.assertEqual(result[4], "app.Main")
        self.assertEqual(result[5:], self._command(bundle)[4:])
        classpath = result[3].split(os.pathsep)
        self.assertEqual(classpath[0], str(self.cli))  # CLI classes keep precedence
        with zipfile.ZipFile(classpath[1]) as shim:
            # Only classes the CLI lacks are supplied.
            self.assertEqual(shim.namelist(), ["org/slf4j/helpers/MarkerIgnoringBase.class"])

    def test_bundle_without_slf4j_is_launched_unchanged(self) -> None:
        bundle = _zip(self.root / "patches-2.mpp", {"patch/Foo.class": b"x"})
        command = self._command(bundle)
        self.assertEqual(cli_launch.prepare_command(command), command)

    def test_bundle_slf4j_already_in_cli_is_launched_unchanged(self) -> None:
        bundle = _zip(self.root / "patches-3.mpp", {
            "org/slf4j/helpers/NamedLoggerBase.class": b"dup",
        })
        command = self._command(bundle)
        self.assertEqual(cli_launch.prepare_command(command), command)

    def test_shim_is_reused_and_can_be_disabled(self) -> None:
        bundle = _zip(self.root / "patches-4.mpp", {"org/slf4j/X.class": b"x"})
        first = cli_launch.prepare_command(self._command(bundle))
        second = cli_launch.prepare_command(self._command(bundle))
        self.assertEqual(first, second)
        with mock.patch.dict(os.environ, {"CLI_SLF4J_SHIM_DISABLED": "true"}):
            self.assertEqual(
                cli_launch.prepare_command(self._command(bundle)), self._command(bundle)
            )

    def test_unusable_inputs_never_break_the_launch(self) -> None:
        for command in (
            [],
            ["python", "-jar", "x"],
            ["java", "-jar"],
            ["java", "-jar", str(self.root / "missing.jar"), "--patches", "x.mpp"],
        ):
            self.assertEqual(cli_launch.prepare_command(command), command)

    def test_cli_without_main_class_is_launched_unchanged(self) -> None:
        cli = _zip(self.root / "nomain.jar", {"META-INF/MANIFEST.MF": "Manifest-Version: 1.0\r\n"})
        bundle = _zip(self.root / "p.mpp", {"org/slf4j/X.class": b"x"})
        command = ["java", "-jar", str(cli), "--patches", str(bundle)]
        self.assertEqual(cli_launch.prepare_command(command), command)

    def test_wrapped_manifest_main_class_is_unfolded(self) -> None:
        cli = _zip(self.root / "wrapped.jar", {
            "META-INF/MANIFEST.MF": "Main-Class: app.very.long.pack\r\n age.MainKt\r\n\r\n",
        })
        self.assertEqual(cli_launch.main_class(cli), "app.very.long.package.MainKt")


if __name__ == "__main__":
    unittest.main()
