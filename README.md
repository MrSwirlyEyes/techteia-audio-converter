# WMA → MP3 Batch Converter (Python 3.9.6 + FFmpeg)

Convert large `.wma` audio libraries to `.mp3` on macOS with maximum quality retention
and full metadata preservation.

---

## Table of Contents

1. [Overview](#overview)
2. [Prerequisites](#prerequisites)
3. [Quality & Metadata Strategy](#quality--metadata-strategy)
4. [Project Structure](#project-structure)
5. [The Script](#the-script)
6. [Running the Conversion](#running-the-conversion)
7. [Verifying Results](#verifying-results)
8. [Troubleshooting](#troubleshooting)
9. [FAQ](#faq)

---

## Overview

**What this does:**

- Recursively scans a source directory for all `.wma` files
- Converts each file to `.mp3` using FFmpeg's `libmp3lame` encoder at highest VBR quality
- Preserves all metadata tags (title, artist, album, track, year, genre, cover art)
- Writes output to a separate directory so originals are never touched
- Runs conversions in parallel across all CPU cores
- Produces a detailed log file and a summary report on completion
- Supports `--dry-run` mode to preview what will happen before committing

**Compatibility:**

| Component | Requirement |
|---|---|
| Python | 3.9.6+ |
| macOS | Ventura, Sonoma, Sequoia, Tahoe |
| FFmpeg | Any recent version via Homebrew |
| Architecture | Apple Silicon (M-series) and Intel |

---

## Prerequisites

### 1. Install Homebrew

If Homebrew is not already installed:

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

### 2. Install FFmpeg

```bash
brew install ffmpeg
```

FFmpeg provides the audio decoder (WMA → PCM) and encoder (PCM → MP3 via `libmp3lame`)
in a single pass. No intermediate files are created.

### 3. Verify Installations

```bash
# Confirm FFmpeg is available and libmp3lame is included
ffmpeg -version
ffmpeg -encoders 2>/dev/null | grep libmp3lame

# Confirm Python version
python3 --version
```

Expected output for the encoder check:
```
A....D libmp3lame          MP3 (MPEG audio layer 3)
```

> **No additional Python packages are required.** The script uses only the standard
> library (`subprocess`, `concurrent.futures`, `argparse`, `logging`, `pathlib`),
> which are all included with Python 3.9.6.

---

## Quality & Metadata Strategy

### Why Lossy-to-Lossy Is Still Worth Doing Carefully

WMA is a lossy format. Re-encoding to MP3 introduces a second generation of compression
loss. You cannot recover quality that WMA already discarded — but you *can* avoid
making things significantly worse by using the highest possible MP3 encoder settings.

### Audio Quality Settings

| Setting | Value | Rationale |
|---|---|---|
| Encoder | `libmp3lame` | Industry-standard; best open-source MP3 quality |
| Mode | VBR `-q:a 0` | Highest VBR quality tier (~220–260 kbps average) |
| Alternative | `-b:a 320k` | CBR 320 kbps if strict bitrate is required |
| Avoid | `-b:a 128k` or lower | Audible degradation on a transcode |

**VBR (`-q:a 0`) is preferred over CBR 320k** because LAME's VBR engine allocates
bits dynamically — more for complex passages, fewer for silence — resulting in better
perceived quality at equal or smaller file sizes.

### Metadata Preservation

The FFmpeg flag `-map_metadata 0` copies the entire metadata container from the source
file into the MP3's ID3v2 tag. This includes:

- Core tags: Title, Artist, Album, Album Artist, Track, Disc, Year, Genre
- Extended tags: Composer, Comment, BPM, Copyright
- Embedded cover art (`WM/Picture` in WMA → `APIC` frame in ID3v2)

> **Cover art note:** FFmpeg handles `WM/Picture` correctly in most cases. If you find
> files missing cover art after conversion, see [Troubleshooting](#troubleshooting).

---

## Project Structure

```
wma_conversion/
├── convert.py          # Main conversion script
├── run.sh              # Optional convenience wrapper
└── logs/               # Auto-created during conversion
    ├── conversion_2025-04-25.log
    └── failed_files.txt
```

---

## The Script

Save this as `convert.py`.

```python
#!/usr/bin/env python3
"""
WMA to MP3 Batch Converter
===========================
Converts .wma audio files to .mp3 using FFmpeg + libmp3lame.

- Maximizes audio quality via LAME VBR mode (-q:a 0)
- Preserves all metadata including cover art (-map_metadata 0)
- Parallel processing via ThreadPoolExecutor
- Dry-run mode, structured logging, and a failed-files report

Python 3.9.6+ | macOS | Requires: ffmpeg (brew install ffmpeg)

Usage:
    python3 convert.py <source_dir> <output_dir> [options]

Examples:
    python3 convert.py ~/Music/WMA ~/Music/MP3
    python3 convert.py ~/Music/WMA ~/Music/MP3 --workers 8
    python3 convert.py ~/Music/WMA ~/Music/MP3 --dry-run
    python3 convert.py ~/Music/WMA ~/Music/MP3 --bitrate 320k
"""

import argparse
import logging
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path


# ── Constants ──────────────────────────────────────────────────────────────────

DEFAULT_WORKERS: int = os.cpu_count() or 4  # Use all available CPU cores
DEFAULT_VBR_QUALITY: str = "0"              # LAME VBR: 0 (best) → 9 (worst)
LOG_DIR: str = "logs"


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


def build_ffmpeg_command(
    input_path: Path,
    output_path: Path,
    vbr_quality: str,
    cbr_bitrate: str,
) -> list:
    """
    Build the FFmpeg command for a single file conversion.

    Quality flags (mutually exclusive — cbr_bitrate takes precedence if set):
        VBR: -q:a 0   → highest quality, variable bitrate (~220–260 kbps avg)
        CBR: -b:a 320k → constant 320 kbps

    Metadata:
        -map_metadata 0  → copy all tags from input container (incl. cover art)
        -id3v2_version 3 → write ID3v2.3 tags (broadest player compatibility)
        -write_id3v1 1   → also write ID3v1 tags for legacy players
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
        "-map_metadata", "0",     # Preserve all source metadata
        "-id3v2_version", "3",    # ID3v2.3 — widest compatibility
        "-write_id3v1", "1",      # Also write ID3v1 for legacy players
        "-y",                     # Overwrite output if it already exists
        str(output_path),
    ]

    return cmd


# ── File Discovery ─────────────────────────────────────────────────────────────

def discover_wma_files(source_dir: Path) -> list:
    """
    Recursively find all .wma files in source_dir.
    Returns a sorted list of Path objects.
    """
    files = sorted(source_dir.rglob("*.wma"))

    # Also catch .WMA (uppercase) for case-sensitive edge cases
    files += sorted(f for f in source_dir.rglob("*.WMA") if f not in files)

    return files


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


# ── Conversion Worker ──────────────────────────────────────────────────────────

def convert_file(
    wma_file: Path,
    source_dir: Path,
    output_dir: Path,
    vbr_quality: str,
    cbr_bitrate: str,
    logger: logging.Logger,
    dry_run: bool,
) -> dict:
    """
    Convert a single .wma file to .mp3.
    Returns a result dict with keys: file, success, error.
    """
    output_path = resolve_output_path(wma_file, source_dir, output_dir)

    if dry_run:
        logger.info(f"[DRY RUN] {wma_file.name} → {output_path}")
        return {"file": wma_file, "success": True, "error": None}

    cmd = build_ffmpeg_command(wma_file, output_path, vbr_quality, cbr_bitrate)
    logger.debug(f"CMD: {' '.join(cmd)}")

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            logger.debug(f"OK: {wma_file.name}")
            return {"file": wma_file, "success": True, "error": None}
        else:
            # Capture last 300 chars of stderr for concise error logging
            error_snippet = result.stderr.strip()[-300:]
            logger.warning(f"FAILED: {wma_file.name}\n  {error_snippet}")
            return {"file": wma_file, "success": False, "error": error_snippet}

    except Exception as exc:
        logger.error(f"EXCEPTION on {wma_file.name}: {exc}")
        return {"file": wma_file, "success": False, "error": str(exc)}


# ── Reporting ──────────────────────────────────────────────────────────────────

def write_failed_report(failed_files: list, log_dir: Path) -> None:
    """Write a plain-text list of failed file paths for easy re-processing."""
    if not failed_files:
        return
    report_path = log_dir / "failed_files.txt"
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(str(r["file"]) for r in failed_files))
    print(f"\nFailed file list written to: {report_path}")


def print_summary(results: list, elapsed_seconds: float) -> None:
    """Print a final summary to stdout."""
    total = len(results)
    success = sum(1 for r in results if r["success"])
    failed = total - success
    mins, secs = divmod(int(elapsed_seconds), 60)

    print("\n" + "─" * 50)
    print(f"  Conversion Complete")
    print("─" * 50)
    print(f"  Total files : {total}")
    print(f"  Succeeded   : {success}")
    print(f"  Failed      : {failed}")
    print(f"  Duration    : {mins}m {secs}s")
    print("─" * 50)

    if failed:
        print(f"\n  {failed} file(s) failed. Check logs/failed_files.txt")


# ── Argument Parsing ───────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Batch convert .wma files to .mp3 with maximum quality.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 convert.py ~/Music/WMA ~/Music/MP3
  python3 convert.py ~/Music/WMA ~/Music/MP3 --workers 8
  python3 convert.py ~/Music/WMA ~/Music/MP3 --bitrate 320k
  python3 convert.py ~/Music/WMA ~/Music/MP3 --dry-run
        """,
    )
    parser.add_argument("source_dir", type=Path, help="Directory containing .wma files")
    parser.add_argument("output_dir", type=Path, help="Directory to write .mp3 files")
    parser.add_argument(
        "--workers", type=int, default=DEFAULT_WORKERS,
        help=f"Parallel workers (default: {DEFAULT_WORKERS} = all CPU cores)"
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
    return parser.parse_args()


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    args = parse_args()

    source_dir: Path = args.source_dir.expanduser().resolve()
    output_dir: Path = args.output_dir.expanduser().resolve()
    log_dir: Path = Path(LOG_DIR)

    # ── Preflight checks ───────────────────────────────────────────────────────
    if not source_dir.is_dir():
        sys.exit(f"ERROR: Source directory not found: {source_dir}")

    if not args.dry_run:
        output_dir.mkdir(parents=True, exist_ok=True)

    check_ffmpeg()

    logger = setup_logging(log_dir)

    # ── Discover files ─────────────────────────────────────────────────────────
    wma_files = discover_wma_files(source_dir)

    if not wma_files:
        logger.info("No .wma files found. Nothing to do.")
        return

    mode_label = "[DRY RUN] " if args.dry_run else ""
    quality_label = (
        f"CBR {args.bitrate}" if args.bitrate else f"VBR q:a {args.quality}"
    )
    logger.info(f"{mode_label}Found {len(wma_files)} .wma file(s)")
    logger.info(f"Source : {source_dir}")
    logger.info(f"Output : {output_dir}")
    logger.info(f"Quality: {quality_label}")
    logger.info(f"Workers: {args.workers}")

    # ── Convert in parallel ────────────────────────────────────────────────────
    results = []
    start_time = datetime.now()

    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {
            executor.submit(
                convert_file,
                wma_file,
                source_dir,
                output_dir,
                args.quality,
                args.bitrate,
                logger,
                args.dry_run,
            ): wma_file
            for wma_file in wma_files
        }

        completed = 0
        for future in as_completed(futures):
            result = future.result()
            results.append(result)
            completed += 1

            status = "✓" if result["success"] else "✗"
            logger.info(
                f"[{completed}/{len(wma_files)}] {status} {result['file'].name}"
            )

    elapsed = (datetime.now() - start_time).total_seconds()

    # ── Report ─────────────────────────────────────────────────────────────────
    failed = [r for r in results if not r["success"]]
    write_failed_report(failed, log_dir)
    print_summary(results, elapsed)


if __name__ == "__main__":
    main()
```

---

## Running the Conversion

### Basic Usage

```bash
python3 convert.py ~/Music/WMA ~/Music/MP3
```

### Dry Run First (Recommended)

Always do a dry run before converting 2,000 files to confirm paths and file counts:

```bash
python3 convert.py ~/Music/WMA ~/Music/MP3 --dry-run
```

### Control Parallel Workers

By default the script uses all CPU cores. Reduce workers if you need the machine
responsive for other tasks during the conversion:

```bash
python3 convert.py ~/Music/WMA ~/Music/MP3 --workers 4
```

### Force CBR 320k Instead of VBR

```bash
python3 convert.py ~/Music/WMA ~/Music/MP3 --bitrate 320k
```

### Re-run Only Failed Files

If any files failed, a `logs/failed_files.txt` report is written. You can feed it
back into the script using a shell loop:

```bash
while IFS= read -r file; do
  python3 convert.py "$(dirname "$file")" ~/Music/MP3
done < logs/failed_files.txt
```

---

## Verifying Results

### Quick Spot-Check via FFprobe

```bash
# Inspect metadata on a converted file
ffprobe -v quiet -print_format json -show_format "~/Music/MP3/Artist/song.mp3"
```

### Check Cover Art Was Preserved

```bash
ffprobe -v quiet -show_streams "~/Music/MP3/Artist/song.mp3" | grep codec_name
# Should output: mp3 and mjpeg (mjpeg = embedded cover art)
```

### Verify File Count Matches

```bash
# Count source WMA files
find ~/Music/WMA -name "*.wma" | wc -l

# Count output MP3 files
find ~/Music/MP3 -name "*.mp3" | wc -l
```

Both counts should match. Any discrepancy means failed conversions — check
`logs/failed_files.txt`.

### Recommended: Validate with MusicBrainz Picard

[MusicBrainz Picard](https://picard.musicbrainz.org/) (free, macOS) gives a visual
confirmation that tags and cover art are intact across your library.

---

## Troubleshooting

### `libmp3lame` not found

```bash
brew reinstall ffmpeg
```

### Permission denied on output directory

```bash
chmod -R u+w ~/Music/MP3
```

### Cover art missing after conversion

Some WMA files embed art in a non-standard way. Force explicit art extraction and
re-embedding with:

```bash
ffmpeg -i input.wma \
  -map 0:a -codec:a libmp3lame -q:a 0 -map_metadata 0 \
  -map 0:v? -c:v copy \
  output.mp3
```

The `-map 0:v?` flag explicitly maps the video stream (cover art) if present.

### Conversion is slow

- Ensure `--workers` matches your core count: `sysctl -n hw.logicalcpu`
- Check Activity Monitor to confirm multiple `ffmpeg` processes are running in parallel
- On Apple Silicon, FFmpeg uses the CPU; the Neural Engine is not involved

### A file fails with `Invalid data found`

The source WMA file may be corrupt. Verify with:

```bash
ffprobe -v error "path/to/file.wma"
```

---

## FAQ

**Q: Will this delete my original WMA files?**
No. The script writes all output to `output_dir`. Source files are never modified
or deleted.

**Q: What happens if an MP3 already exists at the output path?**
FFmpeg will overwrite it (the `-y` flag). To skip existing files instead, add
`-n` in place of `-y` in `build_ffmpeg_command()`.

**Q: Can I convert to a different format (AAC, FLAC, etc.)?**
Yes — change `-codec:a libmp3lame` to `-codec:a aac` (AAC) or `-codec:a flac`
(lossless FLAC) and update the output suffix in `resolve_output_path()`.

**Q: How long will 2,000 files take?**
On an M-series Mac using all cores, expect approximately **5–15 minutes** depending
on file sizes and average source bitrate.

**Q: Is VBR MP3 compatible with all players?**
Yes. LAME VBR has been universally supported by iTunes/Music, Spotify, VLC, and
virtually all hardware players since the early 2000s.
