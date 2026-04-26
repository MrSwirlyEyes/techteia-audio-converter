#!/usr/bin/env python3
"""
WMA to MP3 Batch Converter
===========================
Converts .wma audio files to .mp3 using FFmpeg + libmp3lame.

- Maximizes audio quality via LAME VBR mode (-q:a 0)
- Preserves all metadata including cover art
- Auto-detects and preserves source sample rate and channel count
- Single-threaded conversion for safety and simplicity
- Validates output files after conversion
- Creates detailed CSV manifest log
- Dry-run mode and confirmation prompts

Python 3.9.6+ | macOS | Requires: ffmpeg (brew install ffmpeg)

Usage:
    python3 convert.py <source_dir> [output_dir] [options]

Examples:
    python3 convert.py ./wma                  # output defaults to ./output
    python3 convert.py ./wma ./mp3
    python3 convert.py ./wma ./mp3 --dry-run
    python3 convert.py ./wma ./mp3 --yes
    python3 convert.py ./wma ./mp3 --bitrate 320k
"""

import argparse
import csv
import json
import logging
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional


# ── Constants ──────────────────────────────────────────────────────────────────

DEFAULT_VBR_QUALITY: str = "0"              # LAME VBR: 0 (best) → 9 (worst)
LOG_DIR: str = "logs"
STATE_FILE: str = "conversion_state.txt"    # Tracks completed files for --resume
ESTIMATED_COMPRESSION_RATIO: float = 0.9    # MP3 typically ~90% of WMA size


# ── Logging Setup ──────────────────────────────────────────────────────────────

def setup_logging(log_dir: Path) -> logging.Logger:
    """Configure logging to both console and a timestamped log file."""
    log_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    log_file = log_dir / f"conversion_{timestamp}.log"

    logger = logging.getLogger("wma_converter")
    logger.setLevel(logging.DEBUG)

    # File handler — full DEBUG output
    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    ))

    # Console handler — INFO and above only
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(logging.Formatter("%(levelname)-8s %(message)s"))

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

    logger.info(f"Log file: {log_file}")
    return logger


# ── FFmpeg Helpers ─────────────────────────────────────────────────────────────

def check_ffmpeg() -> None:
    """Verify FFmpeg is installed and libmp3lame is available. Exits on failure."""
    try:
        result = subprocess.run(
            ["ffmpeg", "-encoders"],
            capture_output=True, text=True, check=True
        )
        if "libmp3lame" not in result.stdout:
            sys.exit(
                "ERROR: FFmpeg is installed but libmp3lame is missing.\n"
                "Reinstall with: brew reinstall ffmpeg"
            )
    except FileNotFoundError:
        sys.exit("ERROR: FFmpeg not found. Install with: brew install ffmpeg")


def get_audio_properties(file_path: Path) -> dict:
    """
    Detect sample rate and channel count from source file using ffprobe.
    Falls back to 48000 Hz / stereo if detection fails.
    """
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v", "quiet",
                "-print_format", "json",
                "-show_streams",
                "-select_streams", "a:0",   # First audio stream only
                str(file_path)
            ],
            capture_output=True,
            text=True,
            check=True
        )
        data = json.loads(result.stdout)
        streams = data.get("streams", [])
        if streams:
            audio = streams[0]
            return {
                "sample_rate": str(audio.get("sample_rate", "48000")),
                "channels": str(audio.get("channels", 2)),
            }
    except (subprocess.CalledProcessError, json.JSONDecodeError, KeyError):
        pass
    return {"sample_rate": "48000", "channels": "2"}


def validate_output(output_path: Path, logger: logging.Logger) -> dict:
    """
    Validate the converted MP3 file.
    Returns dict with: valid (bool), error (str), bitrate (str), duration (float)
    """
    result = {
        "valid": False,
        "error": None,
        "bitrate": None,
        "duration": None
    }

    # Check file exists and has size > 0
    if not output_path.exists():
        result["error"] = "Output file does not exist"
        return result

    file_size = output_path.stat().st_size
    if file_size == 0:
        result["error"] = "Output file is empty (0 bytes)"
        return result

    # Use ffprobe to validate file is readable and get metadata
    try:
        probe_result = subprocess.run(
            [
                "ffprobe",
                "-v", "quiet",
                "-print_format", "json",
                "-show_format",
                "-show_streams",
                str(output_path)
            ],
            capture_output=True,
            text=True,
            check=True
        )
        data = json.loads(probe_result.stdout)

        # Check for audio stream
        streams = data.get("streams", [])
        audio_stream = next((s for s in streams if s.get("codec_type") == "audio"), None)

        if not audio_stream:
            result["error"] = "No audio stream found in output file"
            return result

        # Get bitrate and duration
        format_data = data.get("format", {})
        result["bitrate"] = format_data.get("bit_rate", "unknown")
        result["duration"] = float(format_data.get("duration", 0))

        # Check if metadata exists (at least one tag)
        tags = format_data.get("tags", {})
        if not tags:
            logger.warning(f"No metadata found in {output_path.name}")

        result["valid"] = True
        return result

    except (subprocess.CalledProcessError, json.JSONDecodeError, ValueError) as e:
        result["error"] = f"FFprobe validation failed: {str(e)}"
        return result


def build_ffmpeg_command(
    input_path: Path,
    output_path: Path,
    vbr_quality: str,
    cbr_bitrate: Optional[str],
    sample_rate: str,
    channels: str,
) -> list:
    """
    Build the FFmpeg command for a single file conversion.

    Quality flags (mutually exclusive — cbr_bitrate takes precedence if set):
        VBR: -q:a 0   → highest quality, variable bitrate (~220–260 kbps avg)
        CBR: -b:a 320k → constant 320 kbps

    Metadata & Cover Art:
        -map_metadata 0      → copy all tags from input container
        -map 0:a             → explicitly map audio stream
        -map 0:v? -c:v copy  → explicitly map and copy cover art (video stream)
        -id3v2_version 3     → write ID3v2.3 tags (broadest player compatibility)
        -write_id3v1 1       → also write ID3v1 tags for legacy players

    Audio Quality Preservation:
        -ar → preserve detected source sample rate
        -ac → preserve detected source channel count
    """
    cmd = [
        "ffmpeg",
        "-i", str(input_path),
        "-codec:a", "libmp3lame",
    ]

    # Quality: prefer CBR if explicitly requested, otherwise use VBR
    if cbr_bitrate:
        cmd += ["-b:a", cbr_bitrate]
    else:
        cmd += ["-q:a", vbr_quality]

    cmd += [
        # Preserve exact source audio properties
        "-ar", sample_rate,      # Sample rate detected from source
        "-ac", channels,         # Channel count detected from source

        # Metadata and cover art
        "-map_metadata", "0",    # Preserve all source metadata
        "-map", "0:a",           # Map audio stream
        "-map", "0:v?",          # Map video stream if present (cover art)
        "-c:v", "copy",          # Copy video stream without re-encoding
        "-id3v2_version", "3",   # ID3v2.3 — widest compatibility
        "-write_id3v1", "1",     # Also write ID3v1 for legacy players

        "-y",                    # Overwrite output if it already exists
        str(output_path),
    ]

    return cmd


# ── File Discovery ─────────────────────────────────────────────────────────────

def discover_wma_files(source_dir: Path) -> list:
    """
    Recursively find all .wma files in source_dir.
    Returns a sorted list of Path objects.
    Handles both lowercase .wma and uppercase .WMA extensions.
    """
    files = list(source_dir.rglob("*.wma"))
    files += [f for f in source_dir.rglob("*.WMA") if f not in files]
    return sorted(files)


def resolve_output_path(wma_file: Path, source_dir: Path, output_dir: Path) -> Path:
    """
    Mirror the source directory structure in output_dir.

    Example:
        source_dir:  /Music/WMA
        wma_file:    /Music/WMA/Rock/Artist/song.wma
        output_dir:  /Music/MP3
        → output:    /Music/MP3/Rock/Artist/song.mp3
    """
    relative = wma_file.relative_to(source_dir)
    output_path = output_dir / relative.with_suffix(".mp3")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    return output_path


def estimate_total_size(wma_files: list, logger: logging.Logger) -> int:
    """
    Estimate total output size in bytes.
    Returns total size estimate.
    """
    total_source_size = sum(f.stat().st_size for f in wma_files)
    estimated_output_size = int(total_source_size * ESTIMATED_COMPRESSION_RATIO)

    logger.info(f"Source files total: {format_bytes(total_source_size)}")
    logger.info(f"Estimated output size: {format_bytes(estimated_output_size)}")

    return estimated_output_size


def format_bytes(bytes_val: int) -> str:
    """Format bytes as human-readable string."""
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if bytes_val < 1024.0:
            return f"{bytes_val:.2f} {unit}"
        bytes_val /= 1024.0
    return f"{bytes_val:.2f} PB"


def check_available_space(output_dir: Path, required_bytes: int, logger: logging.Logger) -> None:
    """Check if enough disk space is available and warn if tight."""
    try:
        stat = shutil.disk_usage(output_dir)
        available = stat.free

        logger.info(f"Available disk space: {format_bytes(available)}")

        if available < required_bytes:
            logger.warning(
                f"WARNING: Insufficient disk space! "
                f"Need ~{format_bytes(required_bytes)}, have {format_bytes(available)}"
            )
        elif available < required_bytes * 1.2:  # Less than 20% buffer
            logger.warning(
                f"WARNING: Disk space is tight. "
                f"Need ~{format_bytes(required_bytes)}, have {format_bytes(available)}"
            )
    except Exception as e:
        logger.warning(f"Could not check disk space: {e}")


# ── Resume State ───────────────────────────────────────────────────────────────

def init_state(log_dir: Path) -> None:
    """Clear the state file at the start of a fresh (non-resume) run."""
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / STATE_FILE).write_text("", encoding="utf-8")


def load_state(log_dir: Path) -> set:
    """Load the set of source paths already successfully converted."""
    state_file = log_dir / STATE_FILE
    if not state_file.exists():
        return set()
    with open(state_file, "r", encoding="utf-8") as f:
        return {line.strip() for line in f if line.strip()}


def append_state(log_dir: Path, source_path: Path) -> None:
    """Record a successfully converted file immediately (crash-safe)."""
    with open(log_dir / STATE_FILE, "a", encoding="utf-8") as f:
        f.write(f"{source_path}\n")


# ── Conversion Worker ──────────────────────────────────────────────────────────

def convert_file(
    wma_file: Path,
    source_dir: Path,
    output_dir: Path,
    vbr_quality: str,
    cbr_bitrate: Optional[str],
    logger: logging.Logger,
    dry_run: bool,
) -> dict:
    """
    Convert a single .wma file to .mp3.
    Returns a result dict with keys: file, output, success, error, size_before,
    size_after, bitrate, duration, conversion_time
    """
    output_path = resolve_output_path(wma_file, source_dir, output_dir)

    result = {
        "file": wma_file,
        "output": output_path,
        "success": False,
        "error": None,
        "size_before": wma_file.stat().st_size,
        "size_after": 0,
        "bitrate": None,
        "duration": None,
        "conversion_time": 0
    }

    if dry_run:
        logger.info(f"[DRY RUN] {wma_file.name} → {output_path}")
        result["success"] = True
        return result

    # Detect source audio properties to preserve them exactly
    audio_props = get_audio_properties(wma_file)
    logger.debug(
        f"Source: {wma_file.name} | "
        f"sample_rate={audio_props['sample_rate']}Hz | "
        f"channels={audio_props['channels']}"
    )

    cmd = build_ffmpeg_command(
        wma_file, output_path, vbr_quality, cbr_bitrate,
        audio_props["sample_rate"], audio_props["channels"]
    )
    logger.debug(f"CMD: {' '.join(cmd)}")

    start_time = datetime.now()

    try:
        proc_result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
        )

        conversion_time = (datetime.now() - start_time).total_seconds()
        result["conversion_time"] = conversion_time

        if proc_result.returncode == 0:
            # Validate output
            validation = validate_output(output_path, logger)

            if validation["valid"]:
                result["success"] = True
                result["size_after"] = output_path.stat().st_size
                result["bitrate"] = validation["bitrate"]
                result["duration"] = validation["duration"]
                logger.debug(f"OK: {wma_file.name} ({conversion_time:.2f}s)")
            else:
                result["error"] = f"Validation failed: {validation['error']}"
                logger.warning(f"FAILED VALIDATION: {wma_file.name} - {validation['error']}")
        else:
            # Capture last 300 chars of stderr for concise error logging
            error_snippet = proc_result.stderr.strip()[-300:]
            result["error"] = error_snippet
            logger.warning(f"FAILED: {wma_file.name}\n  {error_snippet}")

    except Exception as exc:
        result["error"] = str(exc)
        result["conversion_time"] = (datetime.now() - start_time).total_seconds()
        logger.error(f"EXCEPTION on {wma_file.name}: {exc}")

    return result


# ── Reporting ──────────────────────────────────────────────────────────────────

def write_manifest(results: list, log_dir: Path) -> None:
    """Write detailed CSV manifest of all conversions."""
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    manifest_path = log_dir / f"manifest_{timestamp}.csv"

    with open(manifest_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "Status",
            "Source File",
            "Output File",
            "Size Before (bytes)",
            "Size After (bytes)",
            "Compression %",
            "Bitrate",
            "Duration (s)",
            "Conversion Time (s)",
            "Error"
        ])

        for r in results:
            compression_pct = (
                f"{(r['size_after'] / r['size_before'] * 100):.1f}"
                if r['size_after'] > 0 else "N/A"
            )

            writer.writerow([
                "SUCCESS" if r["success"] else "FAILED",
                str(r["file"]),
                str(r["output"]),
                r["size_before"],
                r["size_after"],
                compression_pct,
                r["bitrate"] or "N/A",
                r["duration"] or "N/A",
                f"{r['conversion_time']:.2f}",
                r["error"] or ""
            ])

    print(f"\nDetailed manifest written to: {manifest_path}")


def write_failed_report(failed_files: list, log_dir: Path) -> None:
    """Write a plain-text list of failed file paths for easy re-processing."""
    if not failed_files:
        return
    report_path = log_dir / "failed_files.txt"
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(str(r["file"]) for r in failed_files))
    print(f"Failed file list written to: {report_path}")


def print_summary(results: list, elapsed_seconds: float) -> None:
    """Print a final summary to stdout."""
    total = len(results)
    success = sum(1 for r in results if r["success"])
    failed = total - success
    mins, secs = divmod(int(elapsed_seconds), 60)

    total_size_before = sum(r["size_before"] for r in results)
    total_size_after = sum(r["size_after"] for r in results if r["success"])

    print("\n" + "─" * 60)
    print(f"  Conversion Complete")
    print("─" * 60)
    print(f"  Total files      : {total}")
    print(f"  Succeeded        : {success}")
    print(f"  Failed           : {failed}")
    print(f"  Duration         : {mins}m {secs}s")
    print(f"  Total size before: {format_bytes(total_size_before)}")
    print(f"  Total size after : {format_bytes(total_size_after)}")
    if total_size_before > 0:
        compression = (total_size_after / total_size_before) * 100
        print(f"  Compression      : {compression:.1f}%")
    print("─" * 60)

    if failed:
        print(f"\n  ⚠ {failed} file(s) failed. Check logs/failed_files.txt")


# ── User Confirmation ──────────────────────────────────────────────────────────

def confirm_conversion(file_count: int, estimated_size: int) -> bool:
    """Ask user to confirm before starting conversion."""
    print("\n" + "═" * 60)
    print("  Ready to Convert")
    print("═" * 60)
    print(f"  Files to convert    : {file_count}")
    print(f"  Estimated output    : {format_bytes(estimated_size)}")
    print("═" * 60)

    response = input("\nProceed with conversion? [y/N]: ").strip().lower()
    return response in ['y', 'yes']


# ── Argument Parsing ───────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Batch convert .wma files to .mp3 with maximum quality.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 convert.py ./wma ./mp3
  python3 convert.py ./wma ./mp3 --dry-run
  python3 convert.py ./wma ./mp3 --yes
  python3 convert.py ./wma ./mp3 --bitrate 320k
        """,
    )
    parser.add_argument("source_dir", type=Path, help="Directory containing .wma files")
    parser.add_argument(
        "output_dir", type=Path, nargs="?", default=None,
        help="Directory to write .mp3 files (default: ./output)"
    )
    parser.add_argument(
        "--quality", type=str, default=DEFAULT_VBR_QUALITY,
        help="LAME VBR quality 0–9 (0=best, default: 0). Ignored if --bitrate is set."
    )
    parser.add_argument(
        "--bitrate", type=str, default=None,
        help="CBR bitrate, e.g. 320k. Overrides --quality. Use only if CBR is required."
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Preview what would be converted without writing any files."
    )
    parser.add_argument(
        "--yes", "-y", action="store_true",
        help="Skip confirmation prompt and start conversion immediately."
    )
    parser.add_argument(
        "--resume", action="store_true",
        help=(
            "Resume a previously interrupted conversion. Skips files already "
            "recorded in logs/conversion_state.txt. Without this flag, all files "
            "are (re-)converted from scratch."
        )
    )
    return parser.parse_args()


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    args = parse_args()

    # Resolve paths — output defaults to <project_root>/output
    script_dir = Path(__file__).parent.resolve()
    source_dir: Path = args.source_dir.expanduser().resolve()
    output_dir: Path = (
        args.output_dir.expanduser().resolve()
        if args.output_dir is not None
        else script_dir / "output"
    )
    log_dir: Path = script_dir / LOG_DIR

    # ── Preflight checks ───────────────────────────────────────────────────────
    if not source_dir.is_dir():
        sys.exit(f"ERROR: Source directory not found: {source_dir}")

    if not args.dry_run:
        output_dir.mkdir(parents=True, exist_ok=True)

    check_ffmpeg()

    logger = setup_logging(log_dir)

    # ── Discover files ─────────────────────────────────────────────────────────
    all_wma_files = discover_wma_files(source_dir)

    if not all_wma_files:
        logger.info("No .wma files found. Nothing to do.")
        return

    # ── Resume / fresh-run state ───────────────────────────────────────────────
    if args.resume and not args.dry_run:
        completed = load_state(log_dir)
        if completed:
            logger.info(f"Resuming: {len(completed)} file(s) already completed, skipping them.")
        else:
            logger.info("--resume specified but no state file found. Starting fresh.")
        wma_files = [f for f in all_wma_files if str(f) not in completed]
        skipped = len(all_wma_files) - len(wma_files)
        if skipped:
            logger.info(f"Skipping {skipped} already-converted file(s).")
    else:
        if not args.dry_run:
            init_state(log_dir)   # Clear state for a fresh run
        wma_files = all_wma_files

    if not wma_files:
        logger.info("All files already converted. Use without --resume to re-convert.")
        return

    mode_label = "[DRY RUN] " if args.dry_run else ""
    quality_label = (
        f"CBR {args.bitrate}" if args.bitrate else f"VBR q:a {args.quality}"
    )

    logger.info(f"{mode_label}Found {len(wma_files)} .wma file(s) to convert")
    logger.info(f"Source : {source_dir}")
    logger.info(f"Output : {output_dir}")
    logger.info(f"Quality: {quality_label}")

    # ── Estimate size and check space ──────────────────────────────────────────
    estimated_size = estimate_total_size(wma_files, logger)

    if not args.dry_run:
        check_available_space(output_dir, estimated_size, logger)

    # ── Confirmation ───────────────────────────────────────────────────────────
    if not args.dry_run and not args.yes:
        if not confirm_conversion(len(wma_files), estimated_size):
            logger.info("Conversion cancelled by user.")
            return

    # ── Convert sequentially ───────────────────────────────────────────────────
    results = []
    start_time = datetime.now()

    for idx, wma_file in enumerate(wma_files, 1):
        logger.info(f"[{idx}/{len(wma_files)}] Converting: {wma_file.name}")

        result = convert_file(
            wma_file,
            source_dir,
            output_dir,
            args.quality,
            args.bitrate,
            logger,
            args.dry_run,
        )
        results.append(result)

        status = "✓" if result["success"] else "✗"
        logger.info(f"[{idx}/{len(wma_files)}] {status} {result['file'].name}")

        # Write to state file immediately after each success (crash-safe resume)
        if result["success"] and not args.dry_run:
            append_state(log_dir, wma_file)

    elapsed = (datetime.now() - start_time).total_seconds()

    # ── Report ─────────────────────────────────────────────────────────────────
    if not args.dry_run:
        write_manifest(results, log_dir)

    failed = [r for r in results if not r["success"]]
    write_failed_report(failed, log_dir)
    print_summary(results, elapsed)


if __name__ == "__main__":
    main()
