"""Launch patch CLIs so that patch bundles embedding slf4j do not crash them.

Background
----------
Morphe/ReVanced CLIs load a patch bundle (``.mpp``/``.rvp``/``patches-*.jar``)
through a ``URLClassLoader`` whose parent is the application class loader.  A
bundle that embeds ``org/slf4j/**`` classes absent from the CLI's own (partial)
slf4j copy makes the JVM define e.g. ``MarkerIgnoringBase`` in the bundle loader
while its superclass ``NamedLoggerBase`` lives in the application loader.  The
two are different runtime packages, so linking fails with::

    java.lang.IllegalAccessError: class org.slf4j.helpers.MarkerIgnoringBase
    cannot access its abstract superclass org.slf4j.helpers.NamedLoggerBase

This is reproducible with every CLI release, so the repository must make the
launch robust instead of depending on a particular CLI/bundle pairing.

Fix
---
When (and only when) a bundle contains slf4j classes the CLI lacks, those
missing classes are copied into a small supplemental JAR that is appended to the
*application* class path::

    java -jar cli.jar ...      ->      java -cp cli.jar:shim.jar <Main-Class> ...

The CLI's own classes keep precedence (they come first), the missing classes now
resolve in the same loader as their superclasses, and bundles without embedded
slf4j are launched exactly as before.  Nothing is pinned or downgraded and no
patch is touched.
"""

from __future__ import annotations

import hashlib
import logging
import os
import tempfile
import zipfile
from pathlib import Path
from typing import Iterable, Sequence

SLF4J_PREFIX = "org/slf4j/"
BUNDLE_SUFFIXES = (".mpp", ".rvp")
SHIM_DIR_ENV = "CLI_SHIM_DIR"
DISABLE_ENV = "CLI_SLF4J_SHIM_DISABLED"


def shim_dir() -> Path:
    override = os.environ.get(SHIM_DIR_ENV, "").strip()
    if override:
        return Path(override)
    return Path(tempfile.gettempdir()) / "morphe-cli-shims"


def _is_bundle(path: Path, cli: Path) -> bool:
    if not path.is_file() or path.resolve() == cli.resolve():
        return False
    name = path.name.casefold()
    return name.endswith(BUNDLE_SUFFIXES) or (
        name.endswith(".jar") and "patch" in name
    )


def _candidate_paths(args: Iterable[str]) -> Iterable[Path]:
    for arg in args:
        value = arg.split("=", 1)[1] if arg.startswith("--") and "=" in arg else arg
        if value and not value.startswith("-"):
            yield Path(value)


def find_bundles(args: Sequence[str], cli: Path) -> list[Path]:
    """Return bundle files referenced by ``args`` (stable order, de-duplicated)."""
    seen: set[Path] = set()
    bundles: list[Path] = []
    for candidate in _candidate_paths(args):
        try:
            if not _is_bundle(candidate, cli):
                continue
            resolved = candidate.resolve()
        except (OSError, ValueError):
            continue
        if resolved not in seen:
            seen.add(resolved)
            bundles.append(candidate)
    return bundles


def _slf4j_entries(archive: zipfile.ZipFile) -> list[str]:
    return [
        name
        for name in archive.namelist()
        if name.startswith(SLF4J_PREFIX) and not name.endswith("/")
    ]


def main_class(cli: Path) -> str | None:
    try:
        with zipfile.ZipFile(cli) as archive:
            manifest = archive.read("META-INF/MANIFEST.MF").decode("utf-8", "replace")
    except (OSError, KeyError, zipfile.BadZipFile):
        return None
    # Manifest lines may be wrapped at 72 columns with a leading space.
    unfolded = manifest.replace("\r\n ", "").replace("\n ", "")
    for line in unfolded.splitlines():
        if line.lower().startswith("main-class:"):
            value = line.split(":", 1)[1].strip()
            return value or None
    return None


def _missing_slf4j(cli: Path, bundles: Sequence[Path]) -> dict[str, tuple[Path, str]]:
    """Map entry name -> (bundle, entry) for slf4j classes the CLI lacks."""
    with zipfile.ZipFile(cli) as archive:
        present = set(archive.namelist())
    missing: dict[str, tuple[Path, str]] = {}
    for bundle in bundles:
        try:
            with zipfile.ZipFile(bundle) as archive:
                for name in _slf4j_entries(archive):
                    if name not in present and name not in missing:
                        missing[name] = (bundle, name)
        except (OSError, zipfile.BadZipFile) as error:
            logging.warning("cannot inspect patch bundle %s: %s", bundle, error)
    return missing


def _digest(cli: Path, bundles: Sequence[Path]) -> str:
    sha = hashlib.sha256()
    for path in (cli, *bundles):
        stat = path.stat()
        sha.update(f"{path.name}:{stat.st_size}:{int(stat.st_mtime)}".encode())
    return sha.hexdigest()[:20]


def build_shim(cli: Path, bundles: Sequence[Path]) -> Path | None:
    """Create (or reuse) the supplemental JAR; ``None`` when none is needed."""
    missing = _missing_slf4j(cli, bundles)
    if not missing:
        return None
    directory = shim_dir()
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"slf4j-shim-{_digest(cli, bundles)}.jar"
    if target.is_file() and target.stat().st_size > 0:
        return target

    handle, temporary_name = tempfile.mkstemp(
        prefix=".slf4j-shim-", suffix=".part", dir=directory
    )
    os.close(handle)
    temporary = Path(temporary_name)
    try:
        opened: dict[Path, zipfile.ZipFile] = {}
        try:
            with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as out:
                for name, (bundle, entry) in sorted(missing.items()):
                    archive = opened.setdefault(bundle, zipfile.ZipFile(bundle))
                    out.writestr(name, archive.read(entry))
        finally:
            for archive in opened.values():
                archive.close()
        temporary.replace(target)  # atomic: a half-written shim is never visible
    finally:
        temporary.unlink(missing_ok=True)
    logging.info(
        "Patch bundle embeds %d slf4j classes the CLI lacks; using class-path shim %s",
        len(missing),
        target,
    )
    return target


def prepare_command(command: Sequence[str]) -> list[str]:
    """Rewrite ``java ... -jar cli ...`` when the shim is required.

    Any problem (unreadable manifest, odd command shape, I/O error) returns the
    original command so the launch behaves exactly as it did before.
    """
    original = list(command)
    if os.environ.get(DISABLE_ENV, "").casefold() == "true":
        return original
    try:
        if not original or Path(original[0]).name not in {"java", "java.exe"}:
            return original
        jar_index = original.index("-jar")
        if jar_index + 1 >= len(original):
            return original
        cli = Path(original[jar_index + 1])
        if not cli.is_file():
            return original
        bundles = find_bundles(original[jar_index + 2 :], cli)
        if not bundles:
            return original
        shim = build_shim(cli, bundles)
        if shim is None:
            return original
        entry_point = main_class(cli)
        if not entry_point:
            logging.warning("CLI %s has no Main-Class; slf4j shim not applied", cli)
            return original
        classpath = os.pathsep.join([str(cli), str(shim)])
        return [
            *original[:jar_index],
            "-cp",
            classpath,
            entry_point,
            *original[jar_index + 2 :],
        ]
    except Exception as error:  # never make a launch worse than before
        logging.warning("slf4j shim preparation failed (%s); launching unchanged", error)
        return original
