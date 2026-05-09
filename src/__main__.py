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
"""

import json
import logging
import re
import subprocess
from os import getenv
from pathlib import Path
from sys import exit

from src import downloader, utils


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
    # Match explicit major version numbers: revanced-cli-4.x, -5.x, -6.x, …
    m = re.search(r"revanced-cli-(\d+)\.", name)
    if m:
        major = int(m.group(1))
        if major == 4:
            return "v4"
        if major >= 6:
            # CLI v6+ uses patcher v22 which is incompatible with patches built
            # against patcher v21 (e.g. YuzuMikan404/linegms-fork-second-).
            # Pin revanced-cli to v5.x in sources/<source>.json to avoid this.
            logging.warning(
                "⚠️  CLI major version is %d (patcher v22+). "
                "Patches built against patcher v21 (e.g. YuzuMikan404) will NOT work. "
                "Pin 'revanced-cli' to 'v5.0.1' in your sources JSON.",
                major,
            )
        return "v5plus"
    # Fallback: any *-all.jar that doesn't carry an explicit version number
    return "legacy"


# ---------------------------------------------------------------------------
# Patch flag helpers
# ---------------------------------------------------------------------------

def _parse_patch_flags(
    patches_txt: Path, cli_ver: str
) -> tuple[list[str], list[str]]:
    """
    Read a patches/<app>-<source>.txt file and return
    (enable_flags, disable_flags) ready to be spliced into a CLI command.

    For Morphe / legacy ReVanced (v3):
      enable  → -i "Name"   (--include)
      disable → -e "Name"   (--exclude)

    For ReVanced v4.x:
      enable  → -i "Name"   (--include)
      disable → -d "Name"   (--disable / not used in v4 but harmless)
      NOTE: In v4, -e means --exclude (not enable!).
            Use -i to include, and --exclusive to suppress all others.

    For ReVanced v5+:
      enable  → -e "Name"   (--enable)
      disable → -d "Name"   (--disable)
    """
    if not patches_txt.exists():
        return [], []

    if cli_ver == "v4":
        # v4: -i = --include (add patch), -e = --exclude (remove patch, NOT enable!)
        enable_flag  = "-i"
        disable_flag = "-e"
    elif cli_ver == "v5plus":
        # v5+: -e = --enable, -d = --disable
        enable_flag  = "-e"
        disable_flag = "-d"
    else:
        # morphe / legacy (v3): -i = include, -e = exclude
        enable_flag  = "-i"
        disable_flag = "-e"

    enables:  list[str] = []
    disables: list[str] = []

    for raw in patches_txt.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("+"):
            enables.extend([enable_flag, line[1:].strip()])
        elif line.startswith("-"):
            disables.extend([disable_flag, line[1:].strip()])

    return enables, disables


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


def _patch_morphe(
    cli: Path,
    bundle: Path,
    input_apk: Path,
    output_apk: Path,
    enables: list[str],
    disables: list[str],
) -> None:
    """Patch using Morphe CLI.

    --continue-on-error is passed so that individual patch fingerprint failures
    (e.g. upstream patch not yet updated for the latest APK version) do not
    abort the entire build.

    NOTE: Morphe CLI does NOT support -e/--exclude or -i/--include flags.
    These flags are silently ignored by the CLI, causing patches/*.txt entries
    to have no effect when the Morphe patching path is taken.
    If enables/disables are non-empty, a warning is emitted so the issue is
    visible in build logs.
    """
    if enables or disables:
        logging.warning(
            "⚠️  patches/*.txt has %d enable(s) and %d disable(s) configured, "
            "but Morphe CLI does NOT support -e/-i patch selection flags — "
            "these entries will be IGNORED. "
            "To apply patch selection, use a ReVanced-based source (revanced-cli + .rvp/.jar bundle) "
            "instead of a Morphe CLI + .mpp bundle combination.",
            len(enables) // 2,
            len(disables) // 2,
        )

    logging.info("Morphe patch cmd enables=%s disables=%s", enables, disables)

    cmd = [
        "java", "-jar", str(cli),
        "patch", "--patches", str(bundle),
        "--out", str(output_apk),
        "--continue-on-error",
        str(input_apk),
    ]
    logging.info("Running: %s", " ".join(cmd))
    try:
        utils.run_process(cmd, stream=True)
    except subprocess.CalledProcessError:
        # Some Morphe versions use a different argument order
        logging.warning("Standard Morphe command failed; retrying with alternative format…")
        retry_cmd = [
            "java", "-jar", str(cli),
            "--patches", str(bundle),
            "--input",   str(input_apk),
            "--output",  str(output_apk),
            "--continue-on-error",
        ]
        logging.info("Retrying: %s", " ".join(retry_cmd))
        utils.run_process(retry_cmd, stream=True)


def _patch_revanced(
    cli: Path,
    bundle: Path,
    input_apk: Path,
    output_apk: Path,
    enables: list[str],
    disables: list[str],
    cli_ver: str = "v5plus",
) -> None:
    """
    Patch using ReVanced CLI v4 or v5+.

    v4.x: patch -b  <bundle> [--exclusive] [-i "Name"] [-e "Name"] --out <out> <input>
          (-b = --bundle)
    v5+:  patch -p  <bundle> [--exclusive] [-e "Name"] [-d "Name"] --out <out> <input>
          (-p = --patches  ※ v5で -b から -p にリネームされた)
    """
    _log_available_patches(cli, bundle)
    logging.info("enable_patches=%s  disable_patches=%s", enables, disables)

    # --exclusive: apply *only* the explicitly enabled patches
    exclusive = ["--exclusive"] if enables else []

    # v4 uses -b/--bundle; v5+ renamed it to -p/--patches
    bundle_flag = "-b" if cli_ver == "v4" else "-p"

    cmd = [
        "java", "-jar", str(cli),
        "patch",
        bundle_flag, str(bundle),
        "--out", str(output_apk),
        *exclusive,
        *disables,
        *enables,
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
) -> None:
    """Patch using ReVanced CLI v3 (legacy *-all.jar without version number)."""
    cmd = [
        "java", "-jar", str(cli),
        "patch", "--patches", str(bundle),
        "--out", str(output_apk),
        *disables, *enables,
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

    # Normalise file name: strip artefact suffixes injected by APKEditor
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

    # ── 7. Parse patch selection ─────────────────────────────────────────────
    patches_txt = Path("patches") / f"{app_name}-{source}.txt"
    enables, disables = _parse_patch_flags(patches_txt, cli_ver)
    logging.info(
        "📋 Patch selection from %s: %d enable(s), %d disable(s)",
        patches_txt.name,
        len(enables) // 2,
        len(disables) // 2,
    )
    if patches_txt.exists() and (enables or disables) and is_morphe:
        logging.warning(
            "⚠️  %s has patch selection rules, but Morphe CLI (.mpp bundle) "
            "does not support -e/-i flags — rules will be ignored. "
            "Consider switching to a ReVanced CLI source for this app.",
            patches_txt.name,
        )

    # ── 8. Repair APK ────────────────────────────────────────────────────────
    logging.info("Checking APK integrity…")
    _repair_apk(input_apk, app_name, version)

    # ── 9. Patch ─────────────────────────────────────────────────────────────
    output_apk = Path(f"{app_name}-{arch}-patch-v{version}.apk")
    logging.info("🔧 Patching with %s CLI (%s)…", cli_ver, cli.name)

    if is_morphe:
        _patch_morphe(cli, bundle, input_apk, output_apk, enables, disables)
    elif cli_ver in ("v4", "v5plus"):
        _patch_revanced(cli, bundle, input_apk, output_apk, enables, disables, cli_ver)
    else:
        _patch_legacy(cli, bundle, input_apk, output_apk, enables, disables)

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

    print(f"\n🎯 {len(built)} / {len(arches)} APK(s) built for '{app_name}':")
    for apk in built:
        print(f"   📱 {Path(apk).name}")

    if failed:
        logging.error("❌ Failed architectures: %s", ", ".join(failed))
        exit(1)


if __name__ == "__main__":
    main()
