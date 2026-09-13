"""
APK build entrypoint.

Workflow:
  1. Download tools (CLI + patch bundle) and input APK.
  2. Apply patches to the untouched signed APK (or signed split base).
  3. Optionally merge the patched base with its split modules via APKEditor.
  4. Strip unwanted native libs for the target architecture.
  5. Sign the patched APK with apksigner.

Supported patching systems
--------------------------
Morphe CLI  (.mpp patch bundle)
  patch --patches <bundle> --out <out> [flags] <input>

ReVanced CLI v4.x  (revanced-cli-4.*.jar)  [patcher v17-v19]
  patch -b <bundle> --out <out> [--exclusive] [-i "Name"] [-e "Name"] <input>
  (-i = --include, -e = --exclude)

ReVanced CLI v5.x  (revanced-cli-5.*.jar)  [patcher v21]  ← use for YuzuMikan404
  patch -b <bundle> --out <out> [--exclusive] [-e "Name"] [-d "Name"] <input>
  (-e = --enable, -d = --disable)

ReVanced CLI v6+   (revanced-cli-6.*.jar)  [patcher v22 — INCOMPATIBLE with v21 patches]
  Same flags as v5, but patch bundles built against patcher v21 will fail to load.

ReVanced CLI legacy / v3  (any other *-all.jar)
  patch --patches <bundle> --out <out> [-i "Name"] [-e "Name"] <input>

patches/<app>-<source>.txt syntax
----------------------------------
  + Patch Name   →  enable / include this patch  (--exclusive mode activated)
  - Patch Name   →  disable / exclude this patch
  # …            →  comment, ignored

my-patch-config.json "options" syntax
--------------------------------------
Each entry in patch_list may carry an optional "options" array:

  {
    "app_name": "youtube",
    "source": "revanced-anddea",
    "options": [
      { "patch": "Custom branding name for YouTube", "key": "appName", "value": "YouTube" },
      { "patch": "Some boolean patch",               "key": "enable",  "value": true },
      { "patch": "Some list patch",                  "key": "items",   "value": ["a","b"] }
    ]
  }

These become  --options=<key>=<value>  arguments passed to the CLI,
matching the behaviour of Enhancify's editOptions()/patchApp() flow.
Options are silently ignored for Morphe CLI (which does not support them).
"""

import fnmatch
import json
import logging
import re
import shutil
import subprocess
import zipfile
from dataclasses import dataclass, field
from os import getenv
from pathlib import Path, PurePosixPath
from sys import exit
from typing import Any, Callable

from src import (
    apk_cache,
    apk_validation,
    cli_compat,
    console_output,
    downloader,
    provenance,
    providers,
    utils,
)


def _console_print(message: str = "") -> None:
    """Write status text without letting a legacy Windows encoding fail a build."""
    console_output.safe_print(message, flush=True)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

class BuildFailure(RuntimeError):
    """A build failure with a stable category for workflow metadata."""

    def __init__(self, category: str, message: str) -> None:
        super().__init__(message)
        self.category = category
        self.message = message

@dataclass
class PatchOption:
    """A single key/value option for a specific patch, as read from my-patch-config.json."""
    patch: str
    key: str
    value: Any  # str | bool | int | list[str]

    def to_cli_flag(self) -> str:
        """Render as  --options=key=value  for ReVanced CLI v5+."""
        v = self.value
        if isinstance(v, bool):
            encoded = "true" if v else "false"
        elif isinstance(v, list):
            # ReVanced CLI expects repeated --options flags for array values;
            # the caller is responsible for expanding lists (see _build_option_flags).
            encoded = str(v[0]) if v else ""
        else:
            encoded = str(v)
        return f"--options={self.key}={encoded}"


@dataclass
class PatchConfig:
    """Parsed representation of one entry in my-patch-config.json."""
    app_name: str
    source: str
    options: list[PatchOption] = field(default_factory=list)
    disable: list[str] = field(default_factory=list)
    force_enable: list[str] = field(default_factory=list)
    required: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict) -> "PatchConfig":
        raw_options = d.get("options") or []
        options = [
            PatchOption(
                patch=o["patch"],
                key=o["key"],
                value=o["value"],
            )
            for o in raw_options
            if "patch" in o and "key" in o and "value" in o
        ]
        disable = d.get("disable") or []
        force_enable = d.get("force_enable") or []
        required = d.get("required") or d.get("required_patches") or []
        return cls(
            app_name=d["app_name"],
            source=d["source"],
            options=options,
            disable=disable,
            force_enable=force_enable,
            required=required,
        )


def _load_patch_config(app_name: str, source: str) -> PatchConfig:
    """Read my-patch-config.json and return the matching PatchConfig (or an empty one)."""
    config_path = Path("my-patch-config.json")
    if not config_path.exists():
        return PatchConfig(app_name=app_name, source=source)

    raw = json.loads(config_path.read_text(encoding="utf-8"))
    for entry in raw.get("patch_list", []):
        if entry.get("app_name") == app_name and entry.get("source") == source:
            return PatchConfig.from_dict(entry)

    return PatchConfig(app_name=app_name, source=source)


# ---------------------------------------------------------------------------
# CLI version detection
# ---------------------------------------------------------------------------

def _cli_version(cli: Path) -> str:
    """Return a simple version tag: 'morphe', 'v4', 'v5plus', or 'legacy'.

    Patcher compatibility:
      CLI v4.x  → patcher v17-v19  (old Patch<BytecodeContext> class style)
      CLI v5.x  → patcher v21      (bytecodePatch DSL)
      CLI v6.x+ → patcher v22+     (BREAKING: incompatible with v21 patches)

    Thin wrapper kept for backward compatibility with existing call sites;
    the actual classification now lives in src/cli_compat.py so it's defined
    in exactly one place.
    """
    return cli_compat.detect_cli_kind(cli)


# ---------------------------------------------------------------------------
# Patch flag helpers
# ---------------------------------------------------------------------------

def _build_patch_flags(
    app_name: str,
    source: str,
    cli_ver: str,
    patch_config: "PatchConfig",
    tools_dir: Path,
) -> tuple[list[str], list[str]]:
    """
    パッチバンドルの patches-list.json から use=true のパッチを自動収集し、
    (enable_flags, disable_flags) を返す。

    patches-list.json が存在しない場合は patches/<app>-<source>.txt にフォールバック。

    For Morphe CLI / ReVanced v5+:
      enable  → -e "Name"
      disable → -d "Name"
    For ReVanced v4.x:
      enable  → -i "Name"
      disable → -e "Name"
    """
    if cli_ver in ("v5plus", "morphe"):
        enable_flag  = "-e"
        disable_flag = "-d"
    else:
        enable_flag  = "-i"
        disable_flag = "-e"

    # パッチバンドルのpkgName取得
    PKG_MAP = {
        ("youtube",       "morphe"):          "com.google.android.youtube",
        ("youtube-music", "morphe"):          "com.google.android.apps.youtube.music",
        ("youtube",       "revanced-anddea"): "com.google.android.youtube",
        ("youtube-music", "revanced-anddea"): "com.google.android.apps.youtube.music",
    }
    pkg_name = PKG_MAP.get((app_name, source)) or providers.configured_package(
        app_name
    )

    # tools/<source>/patches-list.json を探す
    patches_list_path = tools_dir / source / "patches-list.json"

    if patches_list_path.exists() and pkg_name:
        try:
            raw = json.loads(patches_list_path.read_text(encoding="utf-8"))
            # Morphe: {"version":..., "patches":[...]}
            # Anddea: {"version":..., "patches":[...]}
            patch_list = raw["patches"] if isinstance(raw, dict) else raw

            disable_set = {d.lower() for d in patch_config.disable}
            force_enable_set = {f for f in patch_config.force_enable}

            enables: list[str] = []
            for patch in patch_list:
                name = patch.get("name", "")
                use  = patch.get("use", patch.get("default", True))
                # compatiblePackages チェック
                compat = patch.get("compatiblePackages") or []
                if isinstance(compat, dict):
                    pkg_names = list(compat.keys())
                else:
                    pkg_names = [c.get("packageName", c.get("name", "")) for c in compat]
                if compat and pkg_name not in pkg_names:
                    continue
                # Patch options configure a selected patch; they do not select it.
                # Only force_enable may opt a non-default patch into the build.
                if not use and name not in force_enable_set:
                    continue
                if name.lower() in disable_set:
                    continue
                enables.extend([enable_flag, name])
            
            # force_enable に指定されているがパッチバンドルに存在しなかったものを警告
            enabled_names = {enables[i+1] for i in range(0, len(enables), 2)}
            for fe in force_enable_set:
                if fe not in enabled_names and fe.lower() not in disable_set:
                    logging.warning("⚠️  force_enable: '%s' not found in patches-list.json", fe)

            # disable はバンドル内に実在する use=true パッチのみ意味を持つ
            # use=false パッチは最初から除外されるので disable 不要
            disables: list[str] = []
            for d in patch_config.disable:
                disables.extend([disable_flag, d])

            logging.info(
                "📋 Dynamic patch selection from patches-list.json: %d enable(s), %d disable(s)",
                len(enables) // 2, len(disables) // 2,
            )
            return enables, disables

        except Exception as e:
            logging.warning("⚠️  Failed to parse patches-list.json: %s — falling back to txt", e)

    # A force_enable list is also the explicit allowlist when a release does
    # not ship patches-list.json. This keeps non-default requested patches from
    # being silently ignored and activates the CLI's exclusive mode.
    if patch_config.force_enable:
        enables: list[str] = []
        for name in patch_config.force_enable:
            enables.extend([enable_flag, name])
        disables: list[str] = []
        for name in patch_config.disable:
            disables.extend([disable_flag, name])
        logging.info(
            "📋 Explicit patch selection from force_enable: %d enable(s), "
            "%d disable(s)",
            len(enables) // 2,
            len(disables) // 2,
        )
        return enables, disables

    # フォールバック: patches/<app>-<source>.txt
    patches_txt = Path("patches") / f"{app_name}-{source}.txt"
    if not patches_txt.exists():
        logging.info(
            "ℹ️  No explicit patch allowlist for %s; using patch bundle defaults",
            patches_txt,
        )
        return [], []

    enables_fb:  list[str] = []
    disables_fb: list[str] = []
    for raw_line in patches_txt.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("+"):
            enables_fb.extend([enable_flag, line[1:].strip()])
        elif line.startswith("-"):
            disables_fb.extend([disable_flag, line[1:].strip()])

    logging.info(
        "📋 Patch selection from %s (fallback): %d enable(s), %d disable(s)",
        patches_txt.name, len(enables_fb) // 2, len(disables_fb) // 2,
    )
    return enables_fb, disables_fb


def _build_option_flags(options: list[PatchOption], cli_ver: str) -> list[str]:
    """
    Convert PatchOption objects into CLI --options=key=value flags.

    Supported by both Morphe CLI and ReVanced CLI v4/v5+.
    Enhancify passes --options= regardless of patch system (mpp or jar),
    so we do the same here — no source-based suppression.

    null values are skipped (value=None means "use CLI default").
    Array values generate one --options flag per element.
    """
    if not options:
        return []

    if cli_ver == "legacy":
        logging.warning(
            "⚠️  %d option(s) defined in my-patch-config.json, but legacy CLI "
            "may not support --options flags — passing them anyway.",
            len(options),
        )

    flags: list[str] = []
    for opt in options:
        v = opt.value
        if v is None:
            # null = CLIデフォルトに委ねる → スキップ
            logging.debug("Skipping null option: [%s] %s", opt.patch, opt.key)
            continue
        if isinstance(v, list):
            for item in v:
                flags.append(f"--options={opt.key}={item}")
        elif isinstance(v, bool):
            flags.append(f"--options={opt.key}={'true' if v else 'false'}")
        else:
            flags.append(f"--options={opt.key}={v}")

    logging.info("🔩 Option flags (%d): %s", len(flags), flags)
    return flags


def _selected_patch_options(
    options: list[PatchOption], enables: list[str]
) -> list[PatchOption]:
    """Return options only for patches selected by the current patch flags.

    Morphe's options-file format contains an ``enabled`` field. Passing an
    option for a patch that was intentionally excluded from ``enables`` would
    therefore select that patch again as a side effect. When explicit enable
    flags are available, keep patch selection and patch configuration separate.

    An empty ``enables`` list means the caller is relying on bundle defaults and
    no explicit selected-patch set is available, so preserve the configured
    options in that fallback mode.
    """
    if not enables:
        return list(options)

    selected = {
        enables[index + 1]
        for index in range(0, len(enables), 2)
        if index + 1 < len(enables)
    }
    filtered = [option for option in options if option.patch in selected]
    ignored = sorted({option.patch for option in options if option.patch not in selected})
    if ignored:
        logging.info(
            "Ignoring options for unselected patch(es): %s",
            ", ".join(ignored),
        )
    return filtered


# ---------------------------------------------------------------------------
# Patching
# ---------------------------------------------------------------------------

# Match the stable English event text rather than java.util.logging's localized
# severity label. The prefix is "INFO"/"SEVERE" on Actions, but is translated
# when a build is reproduced under another system locale.
_FAILED_PATCH_RE = re.compile(r"\bFAILED:\s*(?P<name>.+?)\s*$", re.IGNORECASE)
_APPLYING_PATCHES_RE = re.compile(
    r"\bApplying\s+(?P<count>\d+)\s+patches?\.\.\.", re.IGNORECASE
)
_APPLIED_PATCH_RE = re.compile(r"\bApplied:\s*(?P<name>.+?)\s*$", re.IGNORECASE)
_FINGERPRINT_FAILURE_RE = re.compile(
    r"PatchException:\s+Failed to match the fingerprint", re.IGNORECASE
)
_MISSING_PATCH_FILE_RE = re.compile(
    r"(?:^|[\\/])apk[\\/]root[\\/](?P<path>[^\r\n(]+?)\s+"
    r"\(No such file or directory\)",
    re.IGNORECASE,
)
_MISSING_PATCH_LIB_RE = re.compile(
    r"\b(?:No\s+)?(?P<path>lib/(?:<abi>|[^/\s]+)/[^\s()]+?\.so)\s+"
    r"(?:not\s+)?found\b",
    re.IGNORECASE,
)


class PatchFailureParser:
    """Extract actual patch outcomes from CLI output."""

    def __init__(self) -> None:
        self.failed_patches: list[str] = []
        self.applied_patches: list[str] = []
        self.applying_count: int | None = None
        self._fingerprint_failure_seen = False
        self._last_failed_patch: str | None = None
        self.missing_paths_by_patch: dict[str, set[str]] = {}

    def __call__(self, line: str) -> None:
        applying_match = _APPLYING_PATCHES_RE.search(line)
        if applying_match:
            self.applying_count = int(applying_match.group("count"))

        applied_match = _APPLIED_PATCH_RE.search(line)
        if applied_match:
            name = applied_match.group("name").strip()
            if name and name not in self.applied_patches:
                self.applied_patches.append(name)
            self._last_failed_patch = None

        failed_match = _FAILED_PATCH_RE.search(line)
        if failed_match:
            name = failed_match.group("name").strip()
            if name and name not in self.failed_patches:
                self.failed_patches.append(name)
            self._last_failed_patch = name or None
        missing_match = _MISSING_PATCH_FILE_RE.search(line)
        if missing_match is None:
            missing_match = _MISSING_PATCH_LIB_RE.search(line)
        if missing_match and self._last_failed_patch:
            path = missing_match.group("path").strip().replace("\\", "/")
            if path:
                self.missing_paths_by_patch.setdefault(
                    self._last_failed_patch, set()
                ).add(path)
        if _FINGERPRINT_FAILURE_RE.search(line):
            self._fingerprint_failure_seen = True

    def result(self) -> list[str]:
        if self._fingerprint_failure_seen and not self.failed_patches:
            return ["Unknown"]
        return list(self.failed_patches)

    def applied_result(self) -> list[str]:
        return list(self.applied_patches)


def _semantic_patch_failure(
    failed_patches: list[str],
    required_patches: list[str],
    applied_patches: list[str],
    applying_count: int | None,
) -> tuple[str, str, list[str]] | None:
    """Return a hard failure when CLI success does not mean full patch success."""

    required_failures = [
        name for name in required_patches if name not in applied_patches
    ]
    count_mismatch = (
        applying_count is not None and applying_count != len(applied_patches)
    )
    if not failed_patches and not required_failures and not count_mismatch:
        return None

    details: list[str] = []
    if failed_patches:
        details.append("CLI reported failed patches: " + ", ".join(failed_patches))
    if required_failures:
        details.append(
            "required patch was not applied: " + ", ".join(required_failures)
        )
    if count_mismatch:
        details.append(
            f"CLI announced {applying_count} patch(es) but reported "
            f"{len(applied_patches)} applied"
        )
    category = (
        "REQUIRED_PATCH_FAILED" if required_failures else "PATCH_APPLY_FAILED"
    )
    return category, "; ".join(details), required_failures

def _log_available_patches(cli: Path, bundle: Path) -> None:
    """Run list-patches and log the output for debugging. Never fatal."""
    try:
        output = utils.run_process(
            [
                "java",
                "-jar",
                str(cli),
                "list-patches",
                f"--patches={bundle}",
            ],
            capture=True, silent=True, check=False,
        )
        if output:
            logging.info("Available patches in %s:\n%s", bundle.name, output)
    except Exception as exc:
        logging.warning("Could not list patches: %s", exc)


def _build_java_args() -> list[str]:
    """Build JVM arguments for Morphe CLI patching.

    Mirrors Enhancify's buildJavaArgs() — tuned for G1GC which is the
    default on GitHub Actions runners (4 vCPU / 16 GB RAM).
    """
    import os
    cpu_cores = os.cpu_count() or 4
    conc_gc_threads = max(2, cpu_cores // 4)

    return [
        "-Djava.awt.headless=true",
        "-Xmx6g",
        "-Xms3g",
        "-Dfile.encoding=UTF-8",
        "-XX:-UsePerfData",
        "-XX:+UseG1GC",
        "-XX:MaxGCPauseMillis=150",
        "-XX:G1HeapRegionSize=2m",
        "-XX:+UseStringDeduplication",
        "-XX:+ParallelRefProcEnabled",
        f"-XX:ConcGCThreads={conc_gc_threads}",
        f"-XX:ParallelGCThreads={cpu_cores}",
        "-XX:CICompilerCount=3",
        "-XX:+UseCompressedOops",
        "-XX:+OptimizeStringConcat",
        "-XX:+DisableExplicitGC",
        "-XX:+TieredCompilation",
        "-XX:ReservedCodeCacheSize=128m",
        "-XX:InitialCodeCacheSize=32m",
        "-XX:MaxMetaspaceSize=128m",
        "-XX:SoftRefLRUPolicyMSPerMB=50",
        "--add-opens=java.base/java.lang=ALL-UNNAMED",
        "--add-opens=java.base/java.util=ALL-UNNAMED",
        "--add-opens=java.base/java.io=ALL-UNNAMED",
    ]


def _patch_morphe(
    cli: Path,
    bundle: Path,
    input_apk: Path,
    output_apk: Path,
    enables: list[str],
    disables: list[str],
    option_flags: list[str],
    patch_options: "list[PatchOption] | None" = None,
    on_output: Callable[[str], None] | None = None,
) -> None:
    """Patch using Morphe CLI.

    v1.8.x flags:
      --patches=<file>        .mpp bundle path (old long form)
      -e / --enable           enable a patch by name
      -d / --disable          disable a patch by name
      -O / --options=         key=value patch options (free-standing)

    v1.9.0-dev.2+ BREAKING CHANGE (ArgGroup restructure):
      -p <file>               .mpp bundle path (new short form; --patches= removed)
      -e / -O / -d            are now NESTED inside the -p ArgGroup:
                                (-p file [(-O k=v)... (-e name | --ei idx)] [-d name]...)
      Consequence: --options= requires a preceding -e/--ei within the same -p block.
                   Passing only -d or only --options without -e causes:
                   "Missing required argument(s): (-e=<name> | --ei=<index>)"

    Strategy for v1.9.0-dev.2+:
      - If there are enables: pair each option_flag with the first -e, then
        add remaining enables/disables in the same -p block.
      - If there are NO enables but there ARE option_flags: use --options-file
        (write a temp JSON) so options can be passed without needing -e.
        Fallback: if neither enables nor option_flags, just -p -d works fine.
      - Disable-only (no enables, no options): works as-is since -d is optional.
    """
    _log_available_patches(cli, bundle)

    logging.info("enable_patches=%s  disable_patches=%s", enables, disables)
    if option_flags:
        logging.info("🔩 Morphe options: %s", option_flags)

    java_args = _build_java_args()

    # Detect CLI argument structure. v1.9.0+ uses a new nested ArgGroup where
    # -e is required alongside -O — this is a structural change, not just a
    # flag rename, so it's still checked via version number (see cli_compat).
    is_v19_plus = cli_compat.is_nested_arggroup_syntax(cli)

    # "--purge" was renamed to "--disable-purge" in a later morphe-cli release
    # (purging scratch files is now the default, so the flag was inverted).
    # Rather than guessing a version cutoff — which breaks again the next
    # time upstream renames something — ask the CLI itself via --help.
    purge_flag = ["--purge"] if cli_compat.supports_flag(cli, "patch", "--purge") else []
    # Current Morphe versions can emit an unsigned APK. The pipeline signs the
    # normalized final artifact itself, so avoid an earlier redundant signing
    # pass when the CLI supports this contract. Older CLIs remain compatible.
    unsigned_flag = (
        ["--unsigned"]
        if cli_compat.supports_flag(cli, "patch", "--unsigned")
        else []
    )

    # --exclusive is only meaningful when patches are explicitly enabled.
    exclusive = ["--exclusive"] if enables else []

    # --bytecode-mode=STRIP_SAFE mirrors Enhancify's G1GC/ParallelGC setting
    # (Enhancify uses STRIP_FAST for SerialGC, STRIP_SAFE for G1GC/ParallelGC).

    if is_v19_plus:
        # v1.9.0-dev.2+: use --options-file (JSON) for options.
        #
        # --options=key=val with -e PatchName works in picocli's ArgGroup only
        # when ALL options for that patch appear between consecutive -e flags.
        # The --options-file approach is the official method: morphe-cli reads
        # it per patch name, so options are always correctly attributed.
        #
        # JSON format (PatchBundle array, same as `options-create` output):
        # [{"meta": {"source": "bundle.mpp"},
        #   "patches": {"PatchName": {"enabled": true, "options": {"key": value}}}}]

        import tempfile as _tempfile, json as _json

        options_file_args: list[str] = []
        tmp_options_path: str | None = None

        if patch_options:
            patches_dict: dict[str, dict] = {}
            for opt in patch_options:
                if opt.value is None:
                    continue
                pname = opt.patch
                if pname not in patches_dict:
                    patches_dict[pname] = {"enabled": True, "options": {}}
                v = opt.value
                if isinstance(v, bool):
                    patches_dict[pname]["options"][opt.key] = v
                elif isinstance(v, list):
                    patches_dict[pname]["options"][opt.key] = v
                elif isinstance(v, int) or isinstance(v, float):
                    patches_dict[pname]["options"][opt.key] = v
                else:
                    patches_dict[pname]["options"][opt.key] = str(v)

            if patches_dict:
                options_json = [{"meta": {"source": bundle.name}, "patches": patches_dict}]
                tmp = _tempfile.NamedTemporaryFile(
                    mode="w", suffix=".json", delete=False, encoding="utf-8"
                )
                _json.dump(options_json, tmp, ensure_ascii=False)
                tmp.flush()
                tmp.close()
                tmp_options_path = tmp.name
                options_file_args = ["--options-file", tmp_options_path]
                logging.info("📄 options-file: %s (%d patches)", tmp_options_path, len(patches_dict))

        cmd = [
            "java", *java_args,
            "-jar", str(cli),
            "patch",
            "--force",
            "--continue-on-error",
            *unsigned_flag,
            *purge_flag,
            "-p", str(bundle),
            f"--out={output_apk}",
            "--bytecode-mode=STRIP_FAST",
            *exclusive,
            *options_file_args,
            *enables,
            *disables,
            str(input_apk),
        ]

    else:
        # v1.8.x legacy syntax
        # --options=key=value must appear immediately after the -e PatchName it belongs to.
        # Build a per-patch option lookup so we can interleave correctly.
        patch_opts_map: dict[str, list[str]] = {}
        if patch_options:
            for opt in patch_options:
                if opt.value is None:
                    continue
                v = opt.value
                if isinstance(v, list):
                    flags = [f"--options={opt.key}={item}" for item in v]
                elif isinstance(v, bool):
                    flags = [f"--options={opt.key}={'true' if v else 'false'}"]
                else:
                    flags = [f"--options={opt.key}={v}"]
                patch_opts_map.setdefault(opt.patch, []).extend(flags)

        # Rebuild enables list interleaved with per-patch options.
        # enables is a flat list like ["-e", "Patch A", "-e", "Patch B", ...]
        interleaved_enables: list[str] = []
        i = 0
        while i < len(enables):
            flag = enables[i]
            interleaved_enables.append(flag)
            if flag == "-e" and i + 1 < len(enables):
                patch_name = enables[i + 1]
                interleaved_enables.append(patch_name)
                interleaved_enables.extend(patch_opts_map.pop(patch_name, []))
                i += 2
            else:
                i += 1
        # Any options whose patch name didn't match an -e (shouldn't happen, but safe fallback)
        leftover_opts: list[str] = []
        for flags in patch_opts_map.values():
            leftover_opts.extend(flags)

        cmd = [
            "java", *java_args,
            "-jar", str(cli),
            "patch",
            "--force",
            *unsigned_flag,
            *purge_flag,
            f"--patches={bundle}",
            f"--out={output_apk}",
            "--bytecode-mode=STRIP_FAST",
            *exclusive,
            *disables,
            *interleaved_enables,
            *leftover_opts,
            str(input_apk),
        ]
    logging.info("Running: %s", " ".join(cmd))
    utils.run_process(cmd, stream=True, on_output=on_output)


def _patch_revanced(
    cli: Path,
    bundle: Path,
    input_apk: Path,
    output_apk: Path,
    enables: list[str],
    disables: list[str],
    option_flags: list[str],
    cli_ver: str = "v5plus",
    on_output: Callable[[str], None] | None = None,
) -> None:
    """
    Patch using ReVanced CLI v4 or v5+.

    v4.x: patch -b <bundle> [--exclusive] [-i "Name"] [-e "Name"] [--options=k=v] --out <out> <input>
    v5+:  patch -p <bundle> [--exclusive] [-e "Name"] [-d "Name"] [--options=k=v] --out <out> <input>
          (-p = --patches  ※ v5で -b から -p にリネームされた)
    """
    _log_available_patches(cli, bundle)
    logging.info("enable_patches=%s  disable_patches=%s", enables, disables)
    if option_flags:
        logging.info("option_flags=%s", option_flags)

    exclusive    = ["--exclusive"] if enables else []
    bundle_flag  = "-b" if cli_ver == "v4" else "-p"

    cmd = [
        "java", "-jar", str(cli),
        "patch",
        "--continue-on-error",
        bundle_flag, str(bundle),
        "--out", str(output_apk),
        *exclusive,
        *disables,
        *enables,
        *option_flags,
        str(input_apk),
    ]
    logging.info("Running: %s", " ".join(cmd))
    utils.run_process(cmd, stream=True, on_output=on_output)


def _patch_legacy(
    cli: Path,
    bundle: Path,
    input_apk: Path,
    output_apk: Path,
    enables: list[str],
    disables: list[str],
    option_flags: list[str],
    on_output: Callable[[str], None] | None = None,
) -> None:
    """Patch using ReVanced CLI v3 (legacy *-all.jar without version number)."""
    if option_flags:
        logging.warning(
            "⚠️  Legacy ReVanced CLI may not support --options flags; "
            "they will be passed anyway: %s",
            option_flags,
        )
    cmd = [
        "java", "-jar", str(cli),
        "patch", "--patches", str(bundle),
        "--continue-on-error",
        "--out", str(output_apk),
        *disables, *enables,
        *option_flags,
        str(input_apk),
    ]
    logging.info("Running: %s", " ".join(cmd))
    utils.run_process(cmd, stream=True, on_output=on_output)


# ---------------------------------------------------------------------------
# APK helpers
# ---------------------------------------------------------------------------

def _split_base_priority(name: str) -> tuple[int, str]:
    """Rank APK modules so the signed install base is patched first."""
    normalized = name.replace("\\", "/").casefold()
    basename = normalized.rsplit("/", 1)[-1]
    if basename == "base.apk":
        return (0, normalized)
    if "base-master" in basename or basename.startswith("base-"):
        return (1, normalized)
    if not any(
        marker in basename
        for marker in ("split_config", "split-", "config.", "config_")
    ):
        return (2, normalized)
    return (3, normalized)


def _extract_split_patch_input(
    input_bundle: Path,
    app_name: str,
    version: str,
) -> tuple[Path, Path]:
    """Extract a split bundle and return its untouched, signed base APK.

    APKEditor necessarily rewrites an APK while merging modules, which removes
    the stock APK signing block. Some patches need that block to read the
    original app certificate. Patch the signed base first and merge its
    configuration modules only after the patcher has finished.
    """
    modules_dir = Path(
        f".build-splits-{_safe_artifact_part(app_name)}-"
        f"v{_safe_artifact_part(version)}"
    )
    shutil.rmtree(modules_dir, ignore_errors=True)
    modules_dir.mkdir(parents=True)

    try:
        with zipfile.ZipFile(input_bundle) as archive:
            members = sorted(
                (
                    name
                    for name in archive.namelist()
                    if name.casefold().endswith(".apk") and not name.endswith("/")
                ),
                key=_split_base_priority,
            )
            if not members:
                raise BuildFailure(
                    "APK_VALIDATION_FAILED",
                    f"split container contains no APK modules: {input_bundle}",
                )
            extracted: dict[str, Path] = {}
            for name in members:
                member_path = PurePosixPath(name.replace("\\", "/"))
                parts = member_path.parts
                if (
                    member_path.is_absolute()
                    or not parts
                    or any(part in {"", ".", ".."} for part in parts)
                ):
                    raise BuildFailure(
                        "APK_VALIDATION_FAILED",
                        f"split container has an unsafe module path: {name}",
                    )
                target = modules_dir.joinpath(*parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(name) as source_file, target.open("wb") as target_file:
                    shutil.copyfileobj(source_file, target_file)
                extracted[name] = target
    except BuildFailure:
        shutil.rmtree(modules_dir, ignore_errors=True)
        raise
    except zipfile.BadZipFile as error:
        shutil.rmtree(modules_dir, ignore_errors=True)
        raise BuildFailure(
            "APK_VALIDATION_FAILED",
            f"split container is not a readable ZIP archive: {input_bundle}",
        ) from error

    base_apk = extracted[members[0]]
    logging.info(
        "Extracted %d split modules; patching signed base first: %s",
        len(members),
        base_apk,
    )
    return base_apk, modules_dir


def _merge_split_modules(
    modules_dir: Path,
    output_apk: Path,
    app_name: str,
) -> None:
    """Merge an already-patched base APK with its original split modules."""
    logging.info("Merging patched base APK with original split modules…")
    apk_editor = downloader.download_apkeditor()
    utils.run_process([
        "java", "-jar", str(apk_editor),
        "m", "-i", str(modules_dir), "-o", str(output_apk),
        "-f",
    ], silent=True)
    if not output_apk.exists():
        logging.error("❌ FATAL: APKEditor produced no output for '%s'", app_name)
        raise BuildFailure("APK_VALIDATION_FAILED", "APKEditor produced no merged APK")
    logging.info("Merged patched APK: %s", output_apk)


def _split_dependent_failures(
    parser: PatchFailureParser,
    modules_dir: Path,
) -> list[str]:
    """Return failed patches whose missing input exists in a split module."""
    wanted = {
        path
        for paths in parser.missing_paths_by_patch.values()
        for path in paths
    }
    if not wanted:
        return []

    available: set[str] = set()
    for module in modules_dir.rglob("*.apk"):
        try:
            with zipfile.ZipFile(module) as archive:
                names = {name.replace("\\", "/") for name in archive.namelist()}
        except zipfile.BadZipFile:
            continue
        for path in wanted:
            pattern = path.replace("<abi>", "*")
            if any(fnmatch.fnmatchcase(name, pattern) for name in names):
                available.add(path)

    return [
        patch
        for patch in parser.failed_patches
        if parser.missing_paths_by_patch.get(patch, set()).intersection(available)
    ]


def _strip_libs(apk: Path, arch: str) -> None:
    """Normalize the patched ZIP and remove libraries outside *arch*."""
    kept_abis: dict[str, set[str]] = {
        # "universal" artifacts support every ARM generation while omitting
        # desktop/emulator and obsolete MIPS libraries.
        "universal": {"arm64-v8a", "armeabi-v7a", "armeabi"},
        "arm64-v8a": {"arm64-v8a"},
        "armeabi-v7a": {"armeabi-v7a", "armeabi"},
    }
    allowed = kept_abis.get(arch)
    if allowed is None:
        return

    temporary = apk.with_name(f".{apk.name}.architecture-filter.tmp")
    temporary.unlink(missing_ok=True)
    try:
        with zipfile.ZipFile(apk) as source:
            entries = source.infolist()
            removed = [
                info.filename
                for info in entries
                if (
                    info.filename.startswith("lib/")
                    and len(info.filename.split("/", 2)) >= 3
                    and info.filename.split("/", 2)[1] not in allowed
                )
            ]
            with zipfile.ZipFile(temporary, "w", allowZip64=True) as target:
                for info in entries:
                    if info.filename in removed:
                        continue
                    target.writestr(info, source.read(info.filename))
        temporary.replace(apk)
        if removed:
            logging.info("Removed %d non-target native libraries", len(removed))
        else:
            logging.info("Normalized patched APK ZIP headers")
    finally:
        temporary.unlink(missing_ok=True)


def _sign_apk(unsigned: Path, signed: Path, app_name: str) -> None:
    """Sign an APK with apksigner. Retries with --min-sdk-version 21 on failure."""
    apksigner = utils.find_apksigner()
    if not apksigner:
        logging.error("❌ FATAL: apksigner not found.")
        exit(1)

    base_cmd = [
        str(apksigner), "sign", "--verbose",
        "--ks",            "keystore/public.jks",
        "--ks-pass",       "pass:public",
        "--key-pass",      "pass:public",
        "--ks-key-alias",  "public",
        "--in",  str(unsigned),
        "--out", str(signed),
    ]

    try:
        utils.run_process(base_cmd, stream=True)
        return
    except Exception as exc:
        logging.warning("Signing attempt 1 failed (%s); retrying with --min-sdk-version 21…", exc)

    try:
        utils.run_process(base_cmd[:3] + ["--min-sdk-version", "21"] + base_cmd[3:], stream=True)
        return
    except Exception as exc2:
        logging.error("❌ FATAL: Both signing attempts failed for '%s': %s", app_name, exc2)
        exit(1)


# ---------------------------------------------------------------------------
# Main build
# ---------------------------------------------------------------------------

def _safe_artifact_part(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-") or "unknown"


def _stage_unmodified_base_apk(
    input_apk: Path,
    app_name: str,
    source: str,
    arch: str,
    version: str,
) -> Path:
    """Preserve the exact provider payload for pre-patch malware scanning."""
    output_dir = Path("base-apk-scan-out")
    output_dir.mkdir(parents=True, exist_ok=True)
    suffix = input_apk.suffix.lower() or ".apk"
    target = output_dir / (
        f"{_safe_artifact_part(app_name)}-"
        f"{_safe_artifact_part(source)}-"
        f"{_safe_artifact_part(arch)}-"
        f"v{_safe_artifact_part(version)}{suffix}"
    )
    temporary = target.with_name(f".{target.name}.part")
    shutil.copy2(input_apk, temporary)
    temporary.replace(target)
    logging.info("Preserved unmodified base APK for VirusTotal: %s", target)
    return target


def _remove_staged_base_apk(app_name: str, source: str, arch: str) -> None:
    output_dir = Path("base-apk-scan-out")
    prefix = (
        f"{_safe_artifact_part(app_name)}-"
        f"{_safe_artifact_part(source)}-"
        f"{_safe_artifact_part(arch)}-v"
    )
    if not output_dir.is_dir():
        return
    for candidate in output_dir.iterdir():
        if candidate.is_file() and candidate.name.startswith(prefix):
            candidate.unlink(missing_ok=True)


def _write_build_report(
    app_name: str,
    source: str,
    version: str,
    source_name: str,
    enables: list[str],
    disables: list[str],
    patch_config: PatchConfig,
    status: str,
    error_category: str | None = None,
    error_summary: str | None = None,
    failed_patches: list[str] | None = None,
    required_patches: list[str] | None = None,
    applied_patches: list[str] | None = None,
    applying_count: int | None = None,
) -> None:
    """Persist patch selection for the workflow's human-readable summary."""
    failed_patches = list(dict.fromkeys(failed_patches or []))
    required_patches = list(dict.fromkeys(required_patches or []))
    if applied_patches is None:
        # Before patch execution there is no actual CLI result yet. Keep the
        # existing selection-based preview for the intermediate report only.
        applied_patches = [
            enables[index + 1]
            for index in range(0, len(enables), 2)
            if index + 1 < len(enables)
            and enables[index + 1] not in failed_patches
        ]
    else:
        applied_patches = list(dict.fromkeys(applied_patches))
    excluded_patches = [
        {
            "name": disables[index + 1],
            "reason": "explicitly disabled in my-patch-config.json or patches allowlist",
        }
        for index in range(0, len(disables), 2)
        if index + 1 < len(disables)
    ]
    requested_patches = sorted(set(patch_config.force_enable))
    missing_requested = [
        {
            "name": name,
            "reason": "requested by force_enable but CLI did not report it as applied",
        }
        for name in requested_patches
        if name not in applied_patches and name not in {item["name"] for item in excluded_patches}
    ]
    feature_failures = excluded_patches + missing_requested
    required_failures = [
        name for name in required_patches
        if name not in applied_patches
    ]
    lifecycle_status = (
        "success_full"
        if status == "success" and not feature_failures and not failed_patches
        else "success_partial"
        if status == "success" and (feature_failures or failed_patches)
        else "failure"
    )
    report = {
        "app_name": app_name,
        "source": source,
        "patch_source": source_name,
        "source_name": source_name,
        "version": version,
        "status": status,
        "lifecycle_status": lifecycle_status,
        "requested_patches": requested_patches,
        "requested_options": [
            {"patch": option.patch, "key": option.key, "value": option.value}
            for option in patch_config.options
        ],
        "applying_count": applying_count,
        "applied_patches": applied_patches,
        "excluded_patches": excluded_patches,
        "disabled_patches": [item["name"] for item in excluded_patches],
        "feature_failures": feature_failures,
        "failed_patches": failed_patches,
        "required_patches": required_patches,
        "required_failures": required_failures,
        "required_patches_satisfied": not required_failures,
        "fully_applied": status == "success" and not feature_failures and not failed_patches,
        "error_category": error_category,
        "error_summary": error_summary,
    }
    path = Path("build-metadata") / "build-report.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_failure_report(
    app_name: str,
    source: str,
    arch: str,
    category: str,
    message: str,
) -> None:
    """Write failure metadata even when the build stopped before patch selection."""
    report = {
        "app_name": app_name,
        "source": source,
        "patch_source": source,
        "source_name": source,
        "architecture": arch,
        "status": "failure",
        "lifecycle_status": "failure",
        "error_category": category,
        "error_summary": message[:500],
    }
    path = Path("build-metadata") / "build-report.json"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        provenance.record_failure(app_name, source, arch, category, message[:500])
    except OSError as error:
        logging.warning("Could not persist build failure metadata: %s", error)


def run_build(app_name: str, source: str, arch: str = "universal") -> str:
    """Download, patch, and sign one APK. Returns the signed APK path."""

    # ── 0. Load patch config (options) ─────────────────────────────────────
    patch_config = _load_patch_config(app_name, source)
    if patch_config.options:
        logging.info(
            "⚙️  Loaded %d option(s) from my-patch-config.json for '%s' × '%s':",
            len(patch_config.options), app_name, source,
        )
        for opt in patch_config.options:
            logging.info("   • [%s] %s = %r", opt.patch, opt.key, opt.value)

    # ── 1. Download tools ───────────────────────────────────────────────────
    try:
        download_files, source_name = downloader.download_required(source)
    except Exception as error:
        raise BuildFailure(
            "PATCH_APPLY_FAILED",
            f"patch tool or bundle download failed: {type(error).__name__}: {error}",
        ) from error

    logging.info("📦 Downloaded %d file(s) for '%s':", len(download_files), source)
    for f in download_files:
        logging.info("   • %s  (%d bytes)", f.name, f.stat().st_size)

    # ── 2. Detect patching system ────────────────────────────────────────────
    # "morphe-cli" という文字列に依存すると上流のリポジトリ改名
    # （例: MorpheApp/morphe-cli → morphe-desktop）で誤判定するため、
    # .mpp拡張子の有無 → source名 の順でフォールバックする。
    is_morphe = any("morphe" in f.name.lower() and f.suffix == ".jar" for f in download_files)
    if not is_morphe:
        is_morphe = any(f.suffix == ".mpp" for f in download_files)
    if not is_morphe:
        is_morphe = "morphe" in source.lower() or "custom" in source.lower()

    logging.info("🔍 Detected: %s patching system", "Morphe" if is_morphe else "ReVanced")

    # ── 3. Locate CLI and patch bundle ───────────────────────────────────────
    if is_morphe:
        cli = (
            utils.find_file(download_files, contains="morphe-cli", suffix=".jar", exclude=["dev"])
            or utils.find_file(download_files, contains="morphe", suffix=".jar")
        )
        bundle = utils.find_latest_patch_bundle(download_files, (".mpp",))
    else:
        cli = utils.find_file(download_files, contains="revanced-cli", suffix=".jar")
        bundle = utils.find_latest_patch_bundle(download_files, (".rvp", ".mpp"))
        bundle = bundle or utils.find_file(
            download_files, contains="patches", suffix=".jar"
        )

    # 最終フォールバック: 上記の名前ベースの判定がすべて外れても、
    # ダウンロード済みファイルの中に.jarが1つしかなければそれをCLIとみなす。
    # （上流の命名規則が完全に変わった場合の保険。誤検出を避けるため
    #  候補が複数ある場合は使わない。）
    if not cli:
        jar_candidates = [f for f in download_files if f.suffix == ".jar"]
        if len(jar_candidates) == 1:
            cli = jar_candidates[0]
            logging.warning(
                "⚠️  CLI jar not matched by name, but exactly one .jar was "
                "downloaded — using it as a fallback: %s", cli.name,
            )

    if not cli:
        logging.error(
            "❌ FATAL: CLI jar not found for source '%s' (is_morphe=%s). "
            "Downloaded files: %s. This usually means the upstream CLI "
            "repository renamed its release asset — check sources/%s.json "
            "and scripts/download_all_tools.py.",
            source, is_morphe, [f.name for f in download_files], source,
        )
        raise BuildFailure("PATCH_APPLY_FAILED", "CLI jar was not found in downloaded tools")
    if not bundle:
        logging.error(
            "❌ FATAL: Patch bundle not found for source '%s' (is_morphe=%s). "
            "Downloaded files: %s.",
            source, is_morphe, [f.name for f in download_files],
        )
        raise BuildFailure("PATCH_APPLY_FAILED", "patch bundle was not found in downloaded tools")

    logging.info("✅ CLI:    %s", cli.name)
    logging.info("✅ Bundle: %s", bundle.name)

    # Re-derive system type from actual files (bundle extension is authoritative)
    if bundle.suffix == ".mpp":
        is_morphe = True
    cli_ver = "morphe" if is_morphe else _cli_version(cli)

    # ── 4. Download input APK ────────────────────────────────────────────────
    input_apk: Path | None = None
    version:   str  | None = None
    package = providers.configured_package(app_name)
    if not package:
        logging.error("❌ FATAL: No package ID configured for '%s'.", app_name)
        exit(1)
    preloaded_apk = getenv("PRE_DOWNLOADED_APK")
    if preloaded_apk:
        preloaded_source = Path(preloaded_apk)
        version = getenv("PRE_DOWNLOADED_VERSION")
        if not preloaded_source.is_file() or not version:
            raise BuildFailure(
                "APK_DOWNLOAD_FAILED",
                "pre-downloaded APK or version is invalid",
            )
        input_apk = Path(f".build-input-{arch}{preloaded_source.suffix}")
        shutil.copy2(preloaded_source, input_apk)
        logging.info("✅ Using pre-downloaded APK input: %s", input_apk)
    else:
        compatible_versions = utils.get_supported_version_candidates(
            package,
            str(cli),
            str(bundle),
        )

        for platform in providers.download_priority(app_name):
            input_apk, version = downloader.download_platform(
                app_name,
                platform,
                str(cli),
                str(bundle),
                arch,
                version_candidates=compatible_versions,
            )
            if input_apk:
                logging.info("✅ APK obtained from %s", platform)
                break

    if input_apk is None and not preloaded_apk:
        fallback_version = next(
            (candidate.canonical for candidate in compatible_versions),
            None,
        )
        if not fallback_version:
            logging.error(
                "❌ FATAL: Could not resolve a fallback version for %s.",
                app_name,
            )
            raise BuildFailure(
                "APK_DOWNLOAD_FAILED",
                f"could not resolve a compatible APK version for {app_name}",
            )
        logging.warning(
            "⚠️  Standard APK providers failed; trying fallback chain for %s v%s",
            package,
            fallback_version,
        )
        try:
            input_apk = downloader.download_with_fallback_chain(
                package,
                fallback_version,
                Path("."),
            )
            version = fallback_version
            if not apk_cache.is_valid_apk_archive(input_apk):
                input_apk.unlink(missing_ok=True)
                raise ValueError("fallback chain returned HTML or a corrupt APK archive")
            apk_cache.stage(input_apk, package, version, "fallback-chain")
            logging.info("✅ fallback chain: downloaded %s v%s -> %s", app_name, version, input_apk.name)
            provenance.record(
                app_name,
                version,
                "fallback-chain",
                input_apk,
                arch,
                config={"package": package},
            )
        except Exception as error:
            logging.error(
                "❌ fallback chain: download failed for %s: %s: %s",
                app_name,
                type(error).__name__,
                error,
            )

    if input_apk is None:
        raise BuildFailure(
            "APK_DOWNLOAD_FAILED",
            f"could not download or validate APK for {app_name} from any provider",
        )

    downloaded_size = input_apk.stat().st_size
    logging.info("[SIZE] downloaded: %d bytes (%s)", downloaded_size, input_apk.name)

    # Preserve the provider payload before extraction, patching, architecture
    # filtering, or signing changes any byte. VirusTotal scans this unmodified
    # download in the release job.
    _stage_unmodified_base_apk(input_apk, app_name, source, arch, version)

    # ── 5. Preserve a signed patch input ────────────────────────────────────
    split_bundle: Path | None = None
    split_modules_dir: Path | None = None
    if input_apk.suffix != ".apk":
        split_bundle = input_apk
        try:
            apk_validation.validate_required_entries(
                split_bundle,
                providers.required_apk_entries(app_name),
            )
        except apk_validation.ApkValidationError as error:
            raise BuildFailure("APK_VALIDATION_FAILED", str(error)) from error
        input_apk, split_modules_dir = _extract_split_patch_input(
            split_bundle, app_name, version
        )

    # Patches can inspect the stock signer certificate. Validate the input, but
    # do not merge, repair, strip, or otherwise rewrite it before patching.
    logging.info("Validating untouched patch input for '%s' architecture…", arch)
    try:
        input_abis = apk_validation.validate_apk(
            input_apk,
            expected_abi=arch,
            validate_app_requirements=split_bundle is None,
        )
    except apk_validation.ApkValidationError as error:
        raise BuildFailure("APK_VALIDATION_FAILED", str(error)) from error
    logging.info(
        "Validated input APK: %s bytes (ABIs: %s)",
        input_apk.stat().st_size,
        ", ".join(sorted(input_abis)) or "none",
    )
    prepared_size = input_apk.stat().st_size
    logging.info("[SIZE] prepared: %d bytes (%s)", prepared_size, input_apk.name)

    # ── 7. Build patch selection (dynamic from patches-list.json) ───────────
    enables, disables = _build_patch_flags(
        app_name=app_name,
        source=source,
        cli_ver=cli_ver,
        patch_config=patch_config,
        tools_dir=Path("tools"),
    )

    # ── 7b. Build option flags ───────────────────────────────────────────────
    selected_options = _selected_patch_options(patch_config.options, enables)
    option_flags = _build_option_flags(selected_options, cli_ver)
    _write_build_report(
        app_name,
        source,
        version,
        source_name,
        enables,
        disables,
        patch_config,
        "patching",
    )

    # ── 8. Patch ─────────────────────────────────────────────────────────────
    output_apk = Path(f"{app_name}-{arch}-patch-v{version}.apk")
    logging.info("🔧 Patching with %s CLI (%s)…", cli_ver, cli.name)
    patch_failure_parser = PatchFailureParser()
    split_retry_parser: PatchFailureParser | None = None

    try:
        if is_morphe:
            _patch_morphe(
                cli, bundle, input_apk, output_apk, enables, disables,
                option_flags, selected_options, patch_failure_parser,
            )
        elif cli_ver in ("v4", "v5plus"):
            _patch_revanced(
                cli, bundle, input_apk, output_apk, enables, disables,
                option_flags, cli_ver, patch_failure_parser,
            )
        else:
            _patch_legacy(
                cli, bundle, input_apk, output_apk, enables, disables,
                option_flags, patch_failure_parser,
            )
    except Exception as error:
        if split_modules_dir is not None:
            shutil.rmtree(split_modules_dir, ignore_errors=True)
        if split_bundle is not None:
            split_bundle.unlink(missing_ok=True)
        raise BuildFailure(
            "PATCH_APPLY_FAILED",
            f"patch CLI failed: {type(error).__name__}: {error}",
        ) from error

    if not output_apk.exists():
        logging.error(
            "❌ FATAL: Patched APK not found after patching (%s). "
            "The patch command likely failed silently.",
            output_apk,
        )
        if split_modules_dir is not None:
            shutil.rmtree(split_modules_dir, ignore_errors=True)
        if split_bundle is not None:
            split_bundle.unlink(missing_ok=True)
        raise BuildFailure("PATCH_APPLY_FAILED", "patch CLI did not produce an APK")

    split_retry_patches: list[str] = []
    if split_modules_dir is not None and is_morphe:
        split_retry_patches = _split_dependent_failures(
            patch_failure_parser, split_modules_dir
        )

    if split_modules_dir is not None and split_bundle is not None:
        # Put the patched base back beside the untouched configuration splits,
        # then merge the complete install set into the final standalone APK.
        shutil.copy2(output_apk, input_apk)
        output_apk.unlink(missing_ok=True)
        try:
            _merge_split_modules(split_modules_dir, output_apk, app_name)

            # Some native patches need a library delivered in an ABI split,
            # while signature patches need the untouched signed base. Preserve
            # both contracts: patch the signed base first, merge its splits,
            # then retry only failures whose missing file is now present.
            if split_retry_patches:
                logging.info(
                    "Retrying split-dependent patch(es) after merge: %s",
                    ", ".join(split_retry_patches),
                )
                retry_input = output_apk.with_name(
                    f".{output_apk.name}.split-retry-input.apk"
                )
                retry_input.unlink(missing_ok=True)
                output_apk.replace(retry_input)
                retry_enables = [
                    item
                    for patch_name in split_retry_patches
                    for item in ("-e", patch_name)
                ]
                retry_options = [
                    option
                    for option in selected_options
                    if option.patch in split_retry_patches
                ]
                retry_option_flags = _build_option_flags(retry_options, cli_ver)
                split_retry_parser = PatchFailureParser()
                try:
                    _patch_morphe(
                        cli,
                        bundle,
                        retry_input,
                        output_apk,
                        retry_enables,
                        [],
                        retry_option_flags,
                        retry_options,
                        split_retry_parser,
                    )
                finally:
                    retry_input.unlink(missing_ok=True)
        finally:
            split_bundle.unlink(missing_ok=True)
            shutil.rmtree(split_modules_dir, ignore_errors=True)
    else:
        input_apk.unlink(missing_ok=True)

    try:
        output_abis = apk_validation.validate_apk(output_apk, expected_abi=arch)
    except apk_validation.ApkValidationError as error:
        raise BuildFailure("APK_VALIDATION_FAILED", str(error)) from error
    logging.info(
        "Validated patched APK: %s bytes (ABIs: %s)",
        output_apk.stat().st_size,
        ", ".join(sorted(output_abis)) or "none",
    )
    # Architecture filtering rewrites the ZIP and therefore invalidates its
    # current signature. Do it only after all patches have read stock metadata;
    # the normal final signing step immediately below signs these exact bytes.
    _strip_libs(output_apk, arch)
    try:
        output_abis = apk_validation.validate_apk(output_apk, expected_abi=arch)
    except apk_validation.ApkValidationError as error:
        raise BuildFailure(
            "APK_VALIDATION_FAILED",
            f"architecture filtering invalidated patched APK: {error}",
        ) from error
    logging.info(
        "Validated architecture-filtered APK (ABIs: %s)",
        ", ".join(sorted(output_abis)) or "none",
    )
    logging.info("[SIZE] patched: %d bytes (%s)", output_apk.stat().st_size, output_apk.name)
    failed_patches = patch_failure_parser.result()
    applied_patches = patch_failure_parser.applied_result()
    if split_retry_parser is not None:
        retry_applied = set(split_retry_parser.applied_result())
        failed_patches = [
            name for name in failed_patches if name not in retry_applied
        ]
        for name in split_retry_parser.result():
            if name not in failed_patches:
                failed_patches.append(name)
        for name in split_retry_patches:
            if name in retry_applied and name not in applied_patches:
                applied_patches.append(name)
    semantic_failure = _semantic_patch_failure(
        failed_patches,
        patch_config.required,
        applied_patches,
        patch_failure_parser.applying_count,
    )
    if semantic_failure is not None:
        category, summary, required_failures = semantic_failure
        _write_build_report(
            app_name,
            source,
            version,
            source_name,
            enables,
            disables,
            patch_config,
            "failure",
            error_category=category,
            error_summary=summary,
            failed_patches=failed_patches,
            required_patches=patch_config.required,
            applied_patches=applied_patches,
            applying_count=patch_failure_parser.applying_count,
        )
        raise BuildFailure(category, summary)
    output_size = output_apk.stat().st_size
    if prepared_size and output_size / prepared_size < 0.25:
        logging.warning(
            "Patched APK is only %.1f%% of the untouched patch input size (%d -> %d bytes); "
            "review the patch output carefully",
            output_size / prepared_size * 100,
            prepared_size,
            output_size,
        )

    # ── 9. Sign ──────────────────────────────────────────────────────────────
    signed_apk = Path(f"{app_name}-{arch}-{source_name}-v{version}.apk")
    _sign_apk(output_apk, signed_apk, app_name)
    output_apk.unlink(missing_ok=True)

    if not signed_apk.exists():
        logging.error("❌ FATAL: Signed APK was not produced for '%s'.", app_name)
        raise BuildFailure("PATCH_APPLY_FAILED", "APK signing did not produce the signed APK")

    try:
        apk_validation.validate_apk(signed_apk, expected_abi=arch)
    except apk_validation.ApkValidationError as error:
        raise BuildFailure("APK_VALIDATION_FAILED", str(error)) from error
    logging.info("[SIZE] signed: %d bytes (%s)", signed_apk.stat().st_size, signed_apk.name)

    _write_build_report(
        app_name,
        source,
        version,
        source_name,
        enables,
        disables,
        patch_config,
        "success",
        failed_patches=failed_patches,
        required_patches=patch_config.required,
        applied_patches=applied_patches,
        applying_count=patch_failure_parser.applying_count,
    )

    _console_print(f"✅ APK built: {signed_apk.name}")
    return str(signed_apk)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    app_name = getenv("APP_NAME")
    source   = getenv("SOURCE")

    if not app_name or not source:
        logging.error("❌ FATAL: APP_NAME and SOURCE environment variables must be set.")
        exit(1)

    # Determine target architectures from arch-config.json
    # arm64 is the primary target. If it cannot be built, the loop below
    # automatically retries universal once as a compatibility fallback.
    arches = ["arm64-v8a"]
    arch_config_path = Path("arch-config.json")

    if arch_config_path.exists():
        arch_config = json.loads(arch_config_path.read_text(encoding="utf-8"))
        if not isinstance(arch_config, list):
            logging.error(
                "arch-config.json must be a JSON array (got %s). "
                "Falling back to arm64-v8a build.",
                type(arch_config).__name__,
            )
        else:
            for entry in arch_config:
                if (
                    isinstance(entry, dict)
                    and entry.get("app_name") == app_name
                    and entry.get("source")   == source
                ):
                    configured = entry.get("arches") or entry.get("arch")
                    if isinstance(configured, str):
                        arches = [configured]
                    elif isinstance(configured, list) and configured:
                        arches = configured
                    break
    else:
        logging.warning("arch-config.json not found — prioritizing arm64-v8a.")

    built:  list[str] = []
    failed: list[str] = []
    build_queue = list(dict.fromkeys(arches))

    for arch in build_queue:
        logging.info("🔨 Building '%s' for %s…", app_name, arch)
        try:
            apk_path = run_build(app_name, source, arch)
            built.append(apk_path)
            _console_print(f"✅ Built {arch}: {Path(apk_path).name}")
            if arch == "universal" and "arm64-v8a" in failed:
                failed.remove("arm64-v8a")
                logging.warning(
                    "⚠️  arm64-v8a failed, but universal fallback succeeded."
                )
        except (SystemExit, Exception) as exc:
            if isinstance(exc, BuildFailure):
                category = exc.category
                summary = exc.message
            else:
                category = "PATCH_APPLY_FAILED"
                summary = f"{type(exc).__name__}: {exc}"
            logging.error("❌ Build failed for '%s' [%s]: %s", app_name, arch, exc)
            downloader.remove_apk_origin(app_name, arch)
            _remove_staged_base_apk(app_name, source, arch)
            _write_failure_report(app_name, source, arch, category, summary)
            failed.append(arch)
            if arch == "arm64-v8a" and "universal" not in build_queue:
                logging.warning(
                    "🛟 Retrying '%s' as universal after arm64-v8a failure.",
                    app_name,
                )
                build_queue.append("universal")

    _console_print(f"\n🎯 {len(built)} APK(s) built for '{app_name}':")
    for apk in built:
        _console_print(f"   📱 {Path(apk).name}")

    if failed:
        logging.error("❌ Failed architectures: %s", ", ".join(failed))
        exit(1)


if __name__ == "__main__":
    main()
