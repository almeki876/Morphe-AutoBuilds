"""
APK build entrypoint.

Workflow:
  1. Download tools (CLI + patch bundle) and input APK.
  2. Optionally merge split APKs via APKEditor.
  3. Strip unwanted native libs for the target architecture.
  4. Apply patches with the appropriate CLI version.
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

import json
import logging
import re
import subprocess
from dataclasses import dataclass, field
from os import getenv
from pathlib import Path
from sys import exit
from typing import Any

from src import downloader, utils


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

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
        return cls(app_name=d["app_name"], source=d["source"], options=options, disable=disable)


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
    """
    name = cli.name.lower()
    if "morphe" in name:
        return "morphe"
    m = re.search(r"revanced-cli-(\d+)\.", name)
    if m:
        major = int(m.group(1))
        if major == 4:
            return "v4"
        if major >= 6:
            logging.warning(
                "⚠️  CLI major version is %d (patcher v22+). "
                "Patches built against patcher v21 (e.g. YuzuMikan404) will NOT work. "
                "Pin 'revanced-cli' to 'v5.0.1' in your sources JSON.",
                major,
            )
        return "v5plus"
    return "legacy"


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
    pkg_name = PKG_MAP.get((app_name, source))

    # tools/<source>/patches-list.json を探す
    patches_list_path = tools_dir / source / "patches-list.json"

    if patches_list_path.exists() and pkg_name:
        try:
            raw = json.loads(patches_list_path.read_text(encoding="utf-8"))
            # Morphe: {"version":..., "patches":[...]}
            # Anddea: {"version":..., "patches":[...]}
            patch_list = raw["patches"] if isinstance(raw, dict) else raw

            # options が設定されているパッチ名セット（use=false でも有効化する）
            config_opts_patches = {o.patch for o in patch_config.options}
            disable_set = {d.lower() for d in patch_config.disable}

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
                # use=false でも options が config に設定されていれば有効化
                if not use and name not in config_opts_patches:
                    continue
                if name.lower() in disable_set:
                    continue
                enables.extend([enable_flag, name])

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

    # フォールバック: patches/<app>-<source>.txt
    patches_txt = Path("patches") / f"{app_name}-{source}.txt"
    if not patches_txt.exists():
        logging.warning("⚠️  No patches-list.json and no %s — no patches selected", patches_txt)
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


# 後方互換エイリアス（直接呼び出し箇所のシグネチャ変更前に残す）
def _parse_patch_flags(patches_txt: Path, cli_ver: str) -> tuple[list[str], list[str]]:
    if not patches_txt.exists():
        return [], []
    enable_flag  = "-e" if cli_ver in ("v5plus", "morphe") else "-i"
    disable_flag = "-d" if cli_ver in ("v5plus", "morphe") else "-e"
    enables, disables = [], []
    for raw in patches_txt.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("+"):
            enables.extend([enable_flag, line[1:].strip()])
        elif line.startswith("-"):
            disables.extend([disable_flag, line[1:].strip()])
    return enables, disables


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


# ---------------------------------------------------------------------------
# Patching
# ---------------------------------------------------------------------------

def _log_available_patches(cli: Path, bundle: Path) -> None:
    """Run list-patches and log the output for debugging. Never fatal."""
    try:
        output = utils.run_process(
            ["java", "-jar", str(cli), "list-patches", str(bundle)],
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

    # Detect CLI version to choose correct argument structure.
    # v1.9.0+ uses a new nested ArgGroup where -e is required alongside -O.
    cli_name = cli.name.lower()
    # Extract version from filename e.g. morphe-cli-1.9.0-dev.3-all.jar
    import re as _re
    ver_match = _re.search(r"morphe-cli-(\d+)\.(\d+)\.(\d+)", cli_name)
    is_v19_plus = False
    if ver_match:
        major, minor = int(ver_match.group(1)), int(ver_match.group(2))
        is_v19_plus = (major, minor) >= (1, 9)

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
            "--purge",
            "-p", str(bundle),
            f"--out={output_apk}",
            "--bytecode-mode=STRIP_SAFE",
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
            "--continue-on-error",
            "--purge",
            f"--patches={bundle}",
            f"--out={output_apk}",
            "--bytecode-mode=STRIP_SAFE",
            *exclusive,
            *disables,
            *interleaved_enables,
            *leftover_opts,
            str(input_apk),
        ]
    logging.info("Running: %s", " ".join(cmd))
    utils.run_process(cmd, stream=True)


def _patch_revanced(
    cli: Path,
    bundle: Path,
    input_apk: Path,
    output_apk: Path,
    enables: list[str],
    disables: list[str],
    option_flags: list[str],
    cli_ver: str = "v5plus",
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
        bundle_flag, str(bundle),
        "--out", str(output_apk),
        *exclusive,
        *disables,
        *enables,
        *option_flags,
        str(input_apk),
    ]
    logging.info("Running: %s", " ".join(cmd))
    utils.run_process(cmd, stream=True)


def _patch_legacy(
    cli: Path,
    bundle: Path,
    input_apk: Path,
    output_apk: Path,
    enables: list[str],
    disables: list[str],
    option_flags: list[str],
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
        "--out", str(output_apk),
        *disables, *enables,
        *option_flags,
        str(input_apk),
    ]
    logging.info("Running: %s", " ".join(cmd))
    utils.run_process(cmd, stream=True)


# ---------------------------------------------------------------------------
# APK helpers
# ---------------------------------------------------------------------------

def _merge_split_apk(input_apk: Path, app_name: str, version: str) -> Path:
    """Merge a split / XAPK into a single APK using APKEditor."""
    logging.warning("Input is not a plain .apk — merging with APKEditor…")
    apk_editor = downloader.download_apkeditor()
    merged = input_apk.with_suffix(".apk")

    utils.run_process([
        "java", "-jar", str(apk_editor),
        "m", "-i", str(input_apk), "-o", str(merged),
    ], silent=True)

    input_apk.unlink(missing_ok=True)

    if not merged.exists():
        logging.error("❌ FATAL: APKEditor produced no output for '%s'", app_name)
        exit(1)

    clean = re.sub(r"\(\d+\)", "", merged.name)
    clean = re.sub(r"-\d+_", "_", clean)
    if clean != merged.name:
        target = merged.with_name(clean)
        merged.rename(target)
        merged = target

    logging.info("Merged APK: %s", merged)
    return merged


def _strip_libs(apk: Path, arch: str) -> None:
    """Remove native libraries that don't belong to *arch*."""
    remove_patterns: dict[str, list[str]] = {
        "universal":    ["lib/x86/*", "lib/x86_64/*"],
        "arm64-v8a":    ["lib/x86/*", "lib/x86_64/*", "lib/armeabi-v7a/*"],
        "armeabi-v7a":  ["lib/x86/*", "lib/x86_64/*", "lib/arm64-v8a/*"],
    }
    patterns = remove_patterns.get(arch)
    if patterns:
        utils.run_process(
            ["zip", "--delete", str(apk)] + patterns,
            silent=True, check=False,
        )


def _repair_apk(apk: Path, app_name: str, version: str) -> None:
    """Attempt to fix APK corruption in-place with 'zip -FF'."""
    try:
        fixed = Path(f"{app_name}-fixed-v{version}.apk")
        subprocess.run(
            ["zip", "-FF", str(apk), "--out", str(fixed)],
            check=False, capture_output=True,
        )
        if fixed.exists() and fixed.stat().st_size > 0:
            apk.unlink(missing_ok=True)
            fixed.rename(apk)
            logging.info("APK integrity check passed.")
    except Exception as exc:
        logging.warning("APK repair skipped: %s", exc)


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
    download_files, source_name = downloader.download_required(source)

    logging.info("📦 Downloaded %d file(s) for '%s':", len(download_files), source)
    for f in download_files:
        logging.info("   • %s  (%d bytes)", f.name, f.stat().st_size)

    # ── 2. Detect patching system ────────────────────────────────────────────
    is_morphe = any("morphe-cli" in f.name.lower() for f in download_files)
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
        bundle = (
            utils.find_file(download_files, contains="patches", suffix=".mpp")
            or utils.find_file(download_files, suffix=".mpp")
        )
    else:
        cli = utils.find_file(download_files, contains="revanced-cli", suffix=".jar")
        bundle = (
            utils.find_file(download_files, contains="patches", suffix=".rvp")
            or utils.find_file(download_files, contains="patches", suffix=".mpp")
            or utils.find_file(download_files, suffix=".mpp")
            or utils.find_file(download_files, contains="patches", suffix=".jar")
        )

    if not cli:
        logging.error("❌ FATAL: CLI jar not found for source '%s'. Files: %s",
                      source, [f.name for f in download_files])
        exit(1)
    if not bundle:
        logging.error("❌ FATAL: Patch bundle not found for source '%s'. Files: %s",
                      source, [f.name for f in download_files])
        exit(1)

    logging.info("✅ CLI:    %s", cli.name)
    logging.info("✅ Bundle: %s", bundle.name)

    # Re-derive system type from actual files (bundle extension is authoritative)
    if bundle.suffix == ".mpp":
        is_morphe = True
    cli_ver = "morphe" if is_morphe else _cli_version(cli)

    # ── 4. Download input APK ────────────────────────────────────────────────
    input_apk: Path | None = None
    version:   str  | None = None

    for method in [
        downloader.download_github,
        downloader.download_apkmirror,
        downloader.download_apkpure,
        downloader.download_uptodown,
        downloader.download_aptoide,
    ]:
        platform = method.__name__.replace("download_", "")
        input_apk, version = method(app_name, str(cli), str(bundle))
        if input_apk:
            logging.info("✅ APK obtained from %s", platform)
            break

    if input_apk is None:
        logging.error("❌ FATAL: Could not download APK for '%s' from any source.", app_name)
        exit(1)

    # ── 5. Merge split APKs (if needed) ─────────────────────────────────────
    if input_apk.suffix != ".apk":
        input_apk = _merge_split_apk(input_apk, app_name, version)

    # ── 6. Strip native libs ─────────────────────────────────────────────────
    logging.info("Processing APK for '%s' architecture…", arch)
    _strip_libs(input_apk, arch)

    # ── 7. Build patch selection (dynamic from patches-list.json) ───────────
    enables, disables = _build_patch_flags(
        app_name=app_name,
        source=source,
        cli_ver=cli_ver,
        patch_config=patch_config,
        tools_dir=Path("tools"),
    )

    # ── 7b. Build option flags ───────────────────────────────────────────────
    option_flags = _build_option_flags(patch_config.options, cli_ver)

    # ── 8. Repair APK ────────────────────────────────────────────────────────
    logging.info("Checking APK integrity…")
    _repair_apk(input_apk, app_name, version)

    # ── 9. Patch ─────────────────────────────────────────────────────────────
    output_apk = Path(f"{app_name}-{arch}-patch-v{version}.apk")
    logging.info("🔧 Patching with %s CLI (%s)…", cli_ver, cli.name)

    if is_morphe:
        _patch_morphe(cli, bundle, input_apk, output_apk, enables, disables, option_flags, patch_config.options)
    elif cli_ver in ("v4", "v5plus"):
        _patch_revanced(cli, bundle, input_apk, output_apk, enables, disables, option_flags, cli_ver)
    else:
        _patch_legacy(cli, bundle, input_apk, output_apk, enables, disables, option_flags)

    input_apk.unlink(missing_ok=True)

    if not output_apk.exists():
        logging.error(
            "❌ FATAL: Patched APK not found after patching (%s). "
            "The patch command likely failed silently.",
            output_apk,
        )
        exit(1)

    # ── 10. Sign ─────────────────────────────────────────────────────────────
    signed_apk = Path(f"{app_name}-{arch}-{source_name}-v{version}.apk")
    _sign_apk(output_apk, signed_apk, app_name)
    output_apk.unlink(missing_ok=True)

    if not signed_apk.exists():
        logging.error("❌ FATAL: Signed APK was not produced for '%s'.", app_name)
        exit(1)

    print(f"✅ APK built: {signed_apk.name}")
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
    arches = ["universal"]
    arch_config_path = Path("arch-config.json")

    if arch_config_path.exists():
        arch_config = json.loads(arch_config_path.read_text(encoding="utf-8"))
        if not isinstance(arch_config, list):
            logging.error(
                "arch-config.json must be a JSON array (got %s). "
                "Falling back to universal build.",
                type(arch_config).__name__,
            )
        else:
            for entry in arch_config:
                if (
                    isinstance(entry, dict)
                    and entry.get("app_name") == app_name
                    and entry.get("source")   == source
                ):
                    arches = entry.get("arches") or entry.get("arch") or arches
                    break
    else:
        logging.warning("arch-config.json not found — building universal only.")

    built:  list[str] = []
    failed: list[str] = []

    for arch in arches:
        logging.info("🔨 Building '%s' for %s…", app_name, arch)
        try:
            apk_path = run_build(app_name, source, arch)
            built.append(apk_path)
            print(f"✅ Built {arch}: {Path(apk_path).name}")
        except SystemExit:
            raise  # propagate fatal errors immediately
        except Exception as exc:
            logging.error("❌ Build failed for '%s' [%s]: %s", app_name, arch, exc)
            failed.append(arch)

    print(f"\n🎯 {len(built)} / {len(arches)} APK(s) built for '{app_name}':")
    for apk in built:
        print(f"   📱 {Path(apk).name}")

    if failed:
        logging.error("❌ Failed architectures: %s", ", ".join(failed))
        exit(1)


if __name__ == "__main__":
    main()
