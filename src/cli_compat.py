"""
CLI compatibility detection — single source of truth.

Why this module exists
-----------------------
Morphe CLI / ReVanced CLI (and forks like Anddea, Hoo-dles, RookieEnough,
Tosox, YuzuMikan404 …) are all moving targets: upstream renames or removes
flags between releases without warning (e.g. "--purge" silently became
"--disable-purge" with inverted default behaviour in morphe-cli 1.10.0).

Previously, flag support was *guessed* from the CLI jar's version number in
three different places (__main__.py x2, utils.py x1). That means every time
upstream renames a flag, EVERY build in the matrix fails identically with
"Unknown option", because a hardcoded version threshold silently goes stale.

This module replaces guessing with a real probe: run the CLI's own --help
and check what it actually says. Version-number branching is kept only for
things that are structural (e.g. Morphe's v1.9 nested ArgGroup syntax, which
--help can't easily expose), not for individual optional flags.
"""

from __future__ import annotations

import logging
import re
from functools import lru_cache
from pathlib import Path

from src import utils

# ---------------------------------------------------------------------------
# CLI "kind" — which subcommand syntax / package family a jar belongs to.
# ---------------------------------------------------------------------------

MORPHE = "morphe"
REVANCED_V4 = "v4"          # patcher v17-v19, uses -b / -i / -e
REVANCED_V5PLUS = "v5plus"  # patcher v21+, uses -p / -e / -d
REVANCED_LEGACY = "legacy"  # unversioned *-all.jar, uses --patches / -i / -e


def detect_cli_kind(cli: Path) -> str:
    """Classify a CLI jar by filename. This only decides *which family* of
    flags to use (-e vs -i, -p vs -b, etc.) — actual optional-flag support
    within that family should be checked with `supports_flag()` below.
    """
    name = cli.name.lower()
    if "morphe" in name:
        return MORPHE

    m = re.search(r"revanced-cli-(\d+)\.", name)
    if m:
        major = int(m.group(1))
        if major == 4:
            return REVANCED_V4
        if major >= 6:
            logging.warning(
                "⚠️  CLI major version is %d (patcher v22+). Patches built "
                "against patcher v21 (e.g. YuzuMikan404) will NOT work. "
                "Pin 'revanced-cli' to 'v5.0.1' in your sources JSON.",
                major,
            )
        return REVANCED_V5PLUS

    return REVANCED_LEGACY


def is_nested_arggroup_syntax(cli: Path) -> bool:
    """True for Morphe CLI >= 1.9.0, where -e/-O/-d moved inside the -p
    ArgGroup (breaking change in argument structure, not just a flag rename —
    this genuinely can't be probed with --help alone, so version numbers are
    the correct tool here).
    """
    m = re.search(r"morphe(?:-[a-z]+)*-(\d+)\.(\d+)\.(\d+)", cli.name.lower())
    if not m:
        return False
    major, minor = int(m.group(1)), int(m.group(2))
    return (major, minor) >= (1, 9)


# ---------------------------------------------------------------------------
# Actual flag-support probing (the part that replaces version guessing).
# ---------------------------------------------------------------------------

@lru_cache(maxsize=None)
def _help_text(cli: str, subcommand: str) -> str:
    """Run `java -jar <cli> <subcommand> --help` once per (cli, subcommand)
    and cache the result. Never raises — an empty string means the optional
    flag support could not be established.
    """
    try:
        output = utils.run_process(
            ["java", "-jar", cli, subcommand, "--help"],
            capture=True, silent=True, check=False,
        )
        return output or ""
    except Exception as exc:
        logging.warning("⚠️  Could not probe '%s %s --help': %s", cli, subcommand, exc)
        return ""


def supports_flag(cli: Path, subcommand: str, flag: str) -> bool:
    """Return True only when the CLI's own help confirms an optional flag.

    Optional compatibility flags must fail closed. If the help probe cannot
    establish support, omitting the flag preserves the CLI's default behaviour
    and avoids turning a probe failure into an `Unknown option` build failure.
    This is especially important for flags such as Morphe's historical
    `--purge`, whose behaviour is now the default and whose old spelling is no
    longer accepted by current releases.
    """
    text = _help_text(str(cli), subcommand)
    if not text:
        logging.warning(
            "⚠️  Could not confirm optional flag %s for '%s %s'; omitting it",
            flag,
            cli,
            subcommand,
        )
        return False
    return flag in text


def clear_probe_cache() -> None:
    """Mainly for tests — drop cached --help output."""
    _help_text.cache_clear()
