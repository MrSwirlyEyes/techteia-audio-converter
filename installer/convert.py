#!/usr/bin/env python3
"""
Audio Batch Converter
======================
Converts audio files to a target format (default: mp3) using FFmpeg.

- Supports any input format FFmpeg can decode
- Adaptive bitrate by default: matches source bitrate per file
- Files already in the target format are copied directly
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
    python3 convert.py ./wma                         # output defaults to ./output, mp3
    python3 convert.py ./wma ./flac --format flac
    python3 convert.py ./wma ./mp3 --dry-run
    python3 convert.py ./wma ./mp3 --yes
    python3 convert.py ./wma ./mp3 --bitrate 320k
"""

import argparse
import csv
import json
import logging
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional


# Suppress console windows when spawning subprocesses from a GUI app on Windows
_NO_WINDOW: dict = (
    {"creationflags": subprocess.CREATE_NO_WINDOW}
    if sys.platform == "win32" else {}
)

# ── Constants ──────────────────────────────────────────────────────────────────

DEFAULT_OUTPUT_FORMAT: str = "mp3"
DEFAULT_VBR_QUALITY: str = "0"             # LAME VBR: 0 (best) → 9 (worst)
LOG_DIR: str = "logs"
STATE_FILE: str = "conversion_state.txt"   # Tracks completed files for --resume
ESTIMATED_COMPRESSION_RATIO: float = 0.9  # Lossy output typically ~90% of source size

# All audio extensions the script will discover and process
AUDIO_EXTENSIONS: frozenset = frozenset({
    ".wma", ".mp3", ".flac", ".wav", ".aac", ".ogg", ".m4a",
    ".opus", ".ape", ".aiff", ".wv", ".ac3", ".dts", ".alac",
})

# Maps output format name → FFmpeg audio codec
CODEC_MAP: dict = {
    "mp3":  "libmp3lame",
    "flac": "flac",
    "wav":  "pcm_s16le",
    "aac":  "aac",
    "m4a":  "aac",
    "ogg":  "libvorbis",
    "opus": "libopus",
    "aiff": "pcm_s16be",
    "alac": "alac",
}

# Lossless formats: quality/bitrate flags are skipped entirely
LOSSLESS_FORMATS: frozenset = frozenset({"flac", "wav", "aiff", "alac"})

# Formats that support embedded cover art via a video stream
COVER_ART_FORMATS: frozenset = frozenset({"mp3", "flac", "m4a", "mp4"})


# ── Logging Setup ──────────────────────────────────────────────────────────────

def setup_logging(log_dir: Path) -> logging.Logger:
    """Configure logging to both console and a timestamped log file."""
    log_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    log_file = log_dir / f"conversion_{timestamp}.log"

    logger = logging.getLogger("audio_converter")
    logger.setLevel(logging.DEBUG)

    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    ))

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(logging.Formatter("%(levelname)-8s %(message)s"))

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

    logger.info(f"Log file: {log_file}")
    return logger


# ── FFmpeg Helpers ─────────────────────────────────────────────────────────────

def check_ffmpeg(output_format: str) -> None:
    """Verify FFmpeg is installed and the required codec for output_format is available."""
    required_codec = CODEC_MAP.get(output_format)
    try:
        result = subprocess.run(
            ["ffmpeg", "-encoders"],
            capture_output=True, text=True, check=True, **_NO_WINDOW
        )
        if required_codec and required_codec not in result.stdout:
            sys.exit(
                f"ERROR: FFmpeg is installed but codec '{required_codec}' is missing "
                f"(required for --format {output_format}).\n"
                f"Reinstall with: brew reinstall ffmpeg"
            )
    except FileNotFoundError:
        sys.exit("ERROR: FFmpeg not found. Install with: brew install ffmpeg")


def get_audio_properties(file_path: Path) -> dict:
    """
    Detect sample rate, channel count, and bitrate from source file using ffprobe.
    Falls back to 48000 Hz / stereo / no bitrate if detection fails.
    """
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v", "quiet",
                "-print_format", "json",
                "-show_streams",
                "-show_format",
                "-select_streams", "a:0",
                str(file_path)
            ],
            capture_output=True,
            text=True,
            check=True,
            **_NO_WINDOW
        )
        data = json.loads(result.stdout)
        streams = data.get("streams", [])
        if streams:
            audio = streams[0]
            # Prefer stream-level bitrate; fall back to container-level
            raw_br = audio.get("bit_rate") or data.get("format", {}).get("bit_rate")
            bit_rate = f"{round(int(raw_br) / 1000)}k" if raw_br else None
            raw_dur = data.get("format", {}).get("duration")
            duration = float(raw_dur) if raw_dur else None
            return {
                "sample_rate": str(audio.get("sample_rate", "48000")),
                "channels": str(audio.get("channels", 2)),
                "bit_rate": bit_rate,
                "duration": duration,
            }
    except (subprocess.CalledProcessError, json.JSONDecodeError, KeyError, ValueError):
        pass
    return {"sample_rate": "48000", "channels": "2", "bit_rate": None, "duration": None}


def validate_output(output_path: Path, logger: logging.Logger) -> dict:
    """
    Validate the output audio file via ffprobe.
    Returns dict with: valid (bool), error (str), bitrate (str), duration (float)
    """
    result = {"valid": False, "error": None, "bitrate": None, "duration": None}

    if not output_path.exists():
        result["error"] = "Output file does not exist"
        return result

    if output_path.stat().st_size == 0:
        result["error"] = "Output file is empty (0 bytes)"
        return result

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
            check=True,
            **_NO_WINDOW
        )
        data = json.loads(probe_result.stdout)

        streams = data.get("streams", [])
        audio_stream = next((s for s in streams if s.get("codec_type") == "audio"), None)

        if not audio_stream:
            result["error"] = "No audio stream found in output file"
            return result

        format_data = data.get("format", {})
        result["bitrate"] = format_data.get("bit_rate", "unknown")
        result["duration"] = float(format_data.get("duration", 0))

        if not format_data.get("tags", {}):
            logger.warning(f"No metadata found in {output_path.name}")

        result["valid"] = True
        return result

    except (subprocess.CalledProcessError, json.JSONDecodeError, ValueError) as e:
        result["error"] = f"FFprobe validation failed: {str(e)}"
        return result


def build_ffmpeg_command(
    input_path: Path,
    output_path: Path,
    output_format: str,
    vbr_quality: str,
    cbr_bitrate: Optional[str],
    sample_rate: str,
    channels: str,
) -> list:
    """
    Build the FFmpeg command for a single file conversion.

    Quality (lossy formats only — lossless formats skip these flags entirely):
        CBR: -b:a <rate>  → when cbr_bitrate is provided (explicit or adaptive match)
        VBR: -q:a <n>     → fallback when no bitrate is available

    Metadata:
        -map_metadata 0   → copy all tags from source container
        -map 0:a          → explicitly map audio stream
        -id3v2_version 3  → ID3v2.3 tags (mp3 only, broadest player compatibility)
        -write_id3v1 1    → ID3v1 tags for legacy players (mp3 only)

    Cover art:
        -map 0:v? -c:v copy  → copy embedded cover art where the format supports it
    """
    codec = CODEC_MAP.get(output_format, "libmp3lame")
    is_lossless = output_format in LOSSLESS_FORMATS

    cmd = ["ffmpeg", "-i", str(input_path), "-codec:a", codec]

    # Quality flags — skipped entirely for lossless formats
    if not is_lossless:
        if cbr_bitrate:
            cmd += ["-b:a", cbr_bitrate]
        else:
            cmd += ["-q:a", vbr_quality]

    cmd += [
        "-ar", sample_rate,
        "-ac", channels,
        "-map_metadata", "0",
        "-map", "0:a",
    ]

    # Cover art: only for formats with embedded video stream support
    if output_format in COVER_ART_FORMATS:
        cmd += ["-map", "0:v?", "-c:v", "copy"]

    # ID3 tags: mp3-specific
    if output_format == "mp3":
        cmd += ["-id3v2_version", "3", "-write_id3v1", "1"]

    cmd += ["-y", str(output_path)]

    return cmd


# ── File Discovery ─────────────────────────────────────────────────────────────

def discover_audio_files(source_dir: Path, recursive: bool = True) -> list:
    """
    Find all supported audio files in source_dir.
    Returns a sorted list of Path objects.
    """
    glob = source_dir.rglob("*") if recursive else source_dir.glob("*")
    return sorted(f for f in glob if f.is_file() and f.suffix.lower() in AUDIO_EXTENSIONS)


def resolve_output_path(
    source_file: Path, source_dir: Path, output_dir: Path, output_suffix: str
) -> Path:
    """
    Mirror the source directory structure in output_dir, replacing the file suffix.

    Example:
        source_dir:   /Music/src
        source_file:  /Music/src/Rock/song.wma
        output_dir:   /Music/out
        output_suffix: .mp3
        → output:     /Music/out/Rock/song.mp3
    """
    relative = source_file.relative_to(source_dir)
    output_path = output_dir / relative.with_suffix(output_suffix)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    return output_path


def estimate_total_size(source_files: list, logger: logging.Logger) -> int:
    """Estimate total output size in bytes for files that will be converted."""
    total_source_size = sum(f.stat().st_size for f in source_files)
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
        elif available < required_bytes * 1.2:
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
    """Load the set of source paths already successfully processed."""
    state_file = log_dir / STATE_FILE
    if not state_file.exists():
        return set()
    with open(state_file, "r", encoding="utf-8") as f:
        return {line.strip() for line in f if line.strip()}


def append_state(log_dir: Path, source_path: Path) -> None:
    """Record a successfully processed file immediately (crash-safe)."""
    with open(log_dir / STATE_FILE, "a", encoding="utf-8") as f:
        f.write(f"{source_path}\n")


# ── Workers ────────────────────────────────────────────────────────────────────

def convert_file(
    source_file: Path,
    source_dir: Path,
    output_dir: Path,
    output_format: str,
    vbr_quality: str,
    cbr_bitrate: Optional[str],
    use_adaptive: bool,
    logger: logging.Logger,
    dry_run: bool,
) -> dict:
    """
    Convert a single audio file to output_format.
    Returns a result dict with keys: file, output, success, error, size_before,
    size_after, bitrate, duration, conversion_time
    """
    output_suffix = f".{output_format}"
    output_path = resolve_output_path(source_file, source_dir, output_dir, output_suffix)

    result = {
        "file": source_file,
        "output": output_path,
        "success": False,
        "error": None,
        "size_before": source_file.stat().st_size,
        "size_after": 0,
        "bitrate": None,
        "duration": None,
        "conversion_time": 0,
    }

    if dry_run:
        logger.info(f"[DRY RUN] {source_file.name} → {output_path.name}")
        result["success"] = True
        return result

    audio_props = get_audio_properties(source_file)
    is_lossless = output_format in LOSSLESS_FORMATS

    # Adaptive bitrate: match source; explicit --bitrate overrides; VBR is fallback.
    # Skipped entirely for lossless output formats.
    if is_lossless:
        effective_bitrate = None
    elif cbr_bitrate:
        effective_bitrate = cbr_bitrate
    elif use_adaptive and audio_props["bit_rate"]:
        effective_bitrate = audio_props["bit_rate"]
    else:
        effective_bitrate = None  # falls through to VBR in build_ffmpeg_command

    quality_str = (
        "lossless" if is_lossless
        else effective_bitrate if effective_bitrate
        else f"VBR q:a {vbr_quality}"
    )
    logger.debug(
        f"Source: {source_file.name} | "
        f"sample_rate={audio_props['sample_rate']}Hz | "
        f"channels={audio_props['channels']} | "
        f"bitrate={audio_props['bit_rate'] or 'unknown'} → {quality_str}"
    )

    cmd = build_ffmpeg_command(
        source_file, output_path, output_format, vbr_quality, effective_bitrate,
        audio_props["sample_rate"], audio_props["channels"]
    )
    logger.debug(f"CMD: {' '.join(cmd)}")

    start_time = datetime.now()

    try:
        proc_result = subprocess.run(cmd, capture_output=True, text=True, **_NO_WINDOW)
        conversion_time = (datetime.now() - start_time).total_seconds()
        result["conversion_time"] = conversion_time

        if proc_result.returncode == 0:
            validation = validate_output(output_path, logger)
            if validation["valid"]:
                result["success"] = True
                result["size_after"] = output_path.stat().st_size
                result["bitrate"] = validation["bitrate"]
                result["duration"] = validation["duration"]
                logger.debug(f"OK: {source_file.name} ({conversion_time:.2f}s)")
            else:
                result["error"] = f"Validation failed: {validation['error']}"
                logger.warning(f"FAILED VALIDATION: {source_file.name} - {validation['error']}")
        else:
            error_snippet = proc_result.stderr.strip()[-300:]
            result["error"] = error_snippet
            logger.warning(f"FAILED: {source_file.name}\n  {error_snippet}")

    except Exception as exc:
        result["error"] = str(exc)
        result["conversion_time"] = (datetime.now() - start_time).total_seconds()
        logger.error(f"EXCEPTION on {source_file.name}: {exc}")

    return result


def copy_file(
    source_file: Path,
    source_dir: Path,
    output_dir: Path,
    logger: logging.Logger,
    dry_run: bool,
) -> dict:
    """Copy a source file directly to output_dir (already in target format)."""
    output_path = resolve_output_path(
        source_file, source_dir, output_dir, source_file.suffix.lower()
    )

    result = {
        "file": source_file,
        "output": output_path,
        "success": False,
        "error": None,
        "size_before": source_file.stat().st_size,
        "size_after": 0,
        "bitrate": None,
        "duration": None,
        "conversion_time": 0,
        "copied": True,
    }

    if dry_run:
        logger.info(f"[DRY RUN] Copy: {source_file.name} → {output_path}")
        result["success"] = True
        result["size_after"] = result["size_before"]
        return result

    try:
        shutil.copy2(str(source_file), str(output_path))
        result["success"] = True
        result["size_after"] = output_path.stat().st_size
        validation = validate_output(output_path, logger)
        if validation["valid"]:
            result["bitrate"] = validation["bitrate"]
            result["duration"] = validation["duration"]
        logger.debug(f"Copied: {source_file.name}")
    except Exception as exc:
        result["error"] = str(exc)
        logger.error(f"COPY FAILED: {source_file.name}: {exc}")

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
            if r["success"]:
                status = "COPIED" if r.get("copied") else "SUCCESS"
            else:
                status = "FAILED"

            writer.writerow([
                status,
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


def print_summary(results: list, elapsed_seconds: float, output_format: str) -> None:
    """Print a final summary to stdout."""
    total = len(results)
    copied = sum(1 for r in results if r.get("copied") and r["success"])
    converted = sum(1 for r in results if not r.get("copied") and r["success"])
    failed = total - copied - converted
    mins, secs = divmod(int(elapsed_seconds), 60)

    total_size_before = sum(r["size_before"] for r in results)
    total_size_after = sum(r["size_after"] for r in results if r["success"])

    print("\n" + "─" * 60)
    print(f"  Conversion Complete  (→ {output_format.upper()})")
    print("─" * 60)
    print(f"  Total files      : {total}")
    print(f"  Converted        : {converted}")
    print(f"  Copied           : {copied}  (already {output_format})")
    print(f"  Failed           : {failed}")
    print(f"  Duration         : {mins}m {secs}s")
    print(f"  Total size before: {format_bytes(total_size_before)}")
    print(f"  Total size after : {format_bytes(total_size_after)}")
    if total_size_before > 0:
        print(f"  Size ratio       : {total_size_after / total_size_before * 100:.1f}%")
    print("─" * 60)

    if failed:
        print(f"\n  ⚠ {failed} file(s) failed. Check logs/failed_files.txt")


# ── User Confirmation ──────────────────────────────────────────────────────────

def confirm_conversion(file_count: int, copy_count: int, estimated_size: int) -> bool:
    """Ask user to confirm before starting."""
    print("\n" + "═" * 60)
    print("  Ready to Process")
    print("═" * 60)
    print(f"  Files to convert    : {file_count}")
    print(f"  Files to copy       : {copy_count}")
    print(f"  Estimated output    : {format_bytes(estimated_size)}")
    print("═" * 60)

    response = input("\nProceed? [y/N]: ").strip().lower()
    return response in ['y', 'yes']


# ── Argument Parsing ───────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    supported = ", ".join(sorted(CODEC_MAP))
    parser = argparse.ArgumentParser(
        description="Batch convert audio files to a target format.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"""
Supported output formats: {supported}

Examples:
  python3 convert.py ./wma ./mp3
  python3 convert.py ./wma ./flac --format flac
  python3 convert.py ./wma ./mp3 --dry-run
  python3 convert.py ./wma ./mp3 --yes
  python3 convert.py ./wma ./mp3 --bitrate 320k
        """,
    )
    parser.add_argument(
        "source_dir", type=Path, nargs="?", default=None,
        help="Directory containing source audio files (default: ./input)"
    )
    parser.add_argument(
        "output_dir", type=Path, nargs="?", default=None,
        help="Directory to write output files (default: ./output)"
    )
    parser.add_argument(
        "--format", type=str, default=DEFAULT_OUTPUT_FORMAT,
        choices=sorted(CODEC_MAP),
        help=f"Output audio format (default: {DEFAULT_OUTPUT_FORMAT})"
    )
    parser.add_argument(
        "--quality", type=str, default=DEFAULT_VBR_QUALITY,
        help=(
            "VBR quality level for the output codec (format-specific scale). "
            "Only used as fallback when adaptive bitrate detection fails and "
            "--bitrate is not set. Ignored for lossless formats."
        )
    )
    parser.add_argument(
        "--bitrate", type=str, default=None,
        help="Fixed CBR bitrate, e.g. 320k. Overrides adaptive mode and --quality."
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Preview what would be processed without writing any files."
    )
    parser.add_argument(
        "--yes", "-y", action="store_true",
        help="Skip confirmation prompt and start immediately."
    )
    parser.add_argument(
        "--no-recursive", action="store_true",
        help="Only scan the top level of source_dir, do not descend into subfolders."
    )
    parser.add_argument(
        "--resume", action="store_true",
        help=(
            "Resume a previously interrupted run. Skips files already recorded "
            "in logs/conversion_state.txt. Without this flag, all files are "
            "(re-)processed from scratch."
        )
    )
    return parser.parse_args()


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    args = parse_args()
    output_format = args.format.lower()

    script_dir = Path(__file__).parent.resolve()
    source_dir: Path = (
        args.source_dir.expanduser().resolve()
        if args.source_dir is not None
        else script_dir / "input"
    )
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

    check_ffmpeg(output_format)

    logger = setup_logging(log_dir)

    # ── Discover files ─────────────────────────────────────────────────────────
    all_source_files = discover_audio_files(source_dir, recursive=not args.no_recursive)

    if not all_source_files:
        logger.info(f"No supported audio files found in {source_dir}. Nothing to do.")
        return

    # ── Resume / fresh-run state ───────────────────────────────────────────────
    if args.resume and not args.dry_run:
        completed = load_state(log_dir)
        if completed:
            logger.info(f"Resuming: {len(completed)} file(s) already completed, skipping them.")
        else:
            logger.info("--resume specified but no state file found. Starting fresh.")
        source_files = [f for f in all_source_files if str(f) not in completed]
        skipped = len(all_source_files) - len(source_files)
        if skipped:
            logger.info(f"Skipping {skipped} already-processed file(s).")
    else:
        if not args.dry_run:
            init_state(log_dir)
        source_files = all_source_files

    if not source_files:
        logger.info("All files already processed. Use without --resume to re-run.")
        return

    # Partition into files to convert vs. copy (already in target format)
    output_ext = f".{output_format}"
    to_convert = [f for f in source_files if f.suffix.lower() != output_ext]
    to_copy    = [f for f in source_files if f.suffix.lower() == output_ext]

    # Adaptive mode: match source bitrate per file.
    # Disabled when user explicitly passes --bitrate (fixed CBR) or --quality (VBR).
    use_adaptive = args.bitrate is None and args.quality == DEFAULT_VBR_QUALITY

    mode_label = "[DRY RUN] " if args.dry_run else ""
    if args.bitrate:
        quality_label = f"CBR {args.bitrate} (fixed)"
    elif output_format in LOSSLESS_FORMATS:
        quality_label = "lossless"
    elif use_adaptive:
        quality_label = "Adaptive CBR (matches source bitrate, VBR fallback)"
    else:
        quality_label = f"VBR q:a {args.quality}"

    logger.info(
        f"{mode_label}Found {len(to_convert)} file(s) to convert, "
        f"{len(to_copy)} file(s) to copy (already {output_format})"
    )
    logger.info(f"Source : {source_dir}")
    logger.info(f"Output : {output_dir}")
    logger.info(f"Format : {output_format.upper()}")
    logger.info(f"Quality: {quality_label}")

    # ── Estimate size and check space ──────────────────────────────────────────
    estimated_size = estimate_total_size(to_convert, logger)

    if not args.dry_run:
        check_available_space(output_dir, estimated_size, logger)

    # ── Confirmation ───────────────────────────────────────────────────────────
    if not args.dry_run and not args.yes:
        if not confirm_conversion(len(to_convert), len(to_copy), estimated_size):
            logger.info("Cancelled by user.")
            return

    # ── Process sequentially ───────────────────────────────────────────────────
    results = []
    start_time = datetime.now()
    total = len(source_files)

    for idx, source_file in enumerate(source_files, 1):
        if source_file.suffix.lower() == output_ext:
            logger.info(f"[{idx}/{total}] Copying:    {source_file.name}")
            result = copy_file(source_file, source_dir, output_dir, logger, args.dry_run)
        else:
            logger.info(f"[{idx}/{total}] Converting: {source_file.name}")
            result = convert_file(
                source_file,
                source_dir,
                output_dir,
                output_format,
                args.quality,
                args.bitrate,
                use_adaptive,
                logger,
                args.dry_run,
            )
        results.append(result)

        status = "✓" if result["success"] else "✗"
        logger.info(f"[{idx}/{total}] {status} {result['file'].name}")

        if result["success"] and not args.dry_run:
            append_state(log_dir, source_file)

    elapsed = (datetime.now() - start_time).total_seconds()

    # ── Report ─────────────────────────────────────────────────────────────────
    if not args.dry_run:
        write_manifest(results, log_dir)

    failed = [r for r in results if not r["success"]]
    write_failed_report(failed, log_dir)
    print_summary(results, elapsed, output_format)


if __name__ == "__main__":
    main()
