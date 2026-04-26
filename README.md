# WMA → MP3 Batch Converter (Python 3.9.6 + FFmpeg)

Convert large `.wma` audio libraries to `.mp3` on macOS with maximum quality retention
and full metadata preservation.

---

## Table of Contents

1. [Overview](#overview)
2. [Prerequisites](#prerequisites)
3. [Quick Start](#quick-start)
4. [Quality & Metadata Strategy](#quality--metadata-strategy)
5. [Project Structure](#project-structure)
6. [Running the Conversion](#running-the-conversion)
7. [Retrying Failed Conversions](#retrying-failed-conversions)
8. [Verifying Results](#verifying-results)
9. [Troubleshooting](#troubleshooting)
10. [FAQ](#faq)

---

## Overview

**What this does:**

- Recursively scans a source directory for all `.wma` files (handles `.wma` and `.WMA`)
- Converts each file to `.mp3` using FFmpeg's `libmp3lame` encoder at highest VBR quality
- **Preserves all metadata tags** (title, artist, album, track, year, genre, **cover art**)
- **Preserves audio properties** (sample rate, channel count)
- **Validates output files** after conversion (checks playability and metadata)
- **Creates detailed CSV manifest** with conversion stats and file sizes
- Writes output to a separate directory so **originals are never touched**
- Single-threaded conversion for safety and reliability
- Shows estimated disk space usage before starting
- Confirmation prompt (can be skipped with `--yes` flag)
- Produces detailed log files and failed-file reports
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

### 4. (Optional) Set Up Virtual Environment

While not required, using a virtual environment is recommended:

```bash
# Create virtual environment
python3 -m venv .venv

# Activate it
source .venv/bin/activate

# When done working, deactivate
deactivate
```

> **No additional Python packages are required.** The script uses only the standard
> library (`subprocess`, `argparse`, `logging`, `pathlib`, `csv`, `json`, etc.),
> which are all included with Python 3.9.6.

---

## Quick Start

The fastest way to get started:

```bash
# 1. Always do a dry run first to preview
./run.sh dry-run

# 2. Convert with confirmation prompt
./run.sh convert

# Or convert without prompting
./run.sh convert-yes

# 3. If any files fail, retry them
./run.sh retry
```

**Before using `run.sh`**, edit the file and set your paths:
```bash
SOURCE_DIR="./wma"     # Your .wma files location
OUTPUT_DIR="./mp3"     # Where .mp3 files will go
```

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
| Sample Rate | `-ar 48k` | Preserved from source (48kHz standard) |
| Channels | `-ac 2` | Stereo (preserved from source) |
| Alternative | `-b:a 320k` | CBR 320 kbps if strict bitrate is required |
| Avoid | `-b:a 128k` or lower | Audible degradation on a transcode |

**VBR (`-q:a 0`) is preferred over CBR 320k** because LAME's VBR engine allocates
bits dynamically — more for complex passages, fewer for silence — resulting in better
perceived quality at equal or smaller file sizes.

### Metadata Preservation

The script uses multiple FFmpeg flags to ensure complete metadata transfer:

- **`-map_metadata 0`** — Copies all metadata tags from source to output
- **`-map 0:a`** — Maps the audio stream
- **`-map 0:v?`** — Maps the video stream if present (this is the cover art!)
- **`-c:v copy`** — Copies cover art without re-encoding
- **`-id3v2_version 3`** — Writes ID3v2.3 tags (broadest compatibility)
- **`-write_id3v1 1`** — Also writes ID3v1 tags for legacy players

This preserves:

- Core tags: Title, Artist, Album, Album Artist, Track, Disc, Year, Genre
- Extended tags: Composer, Comment, BPM, Copyright
- **Embedded cover art** (`WM/Picture` in WMA → `APIC` frame in ID3v2)

### Output Validation

After each conversion, the script automatically validates:

✓ Output file exists and has size > 0
✓ File is readable by FFprobe
✓ Contains a valid audio stream
✓ Metadata is present
✓ Bitrate and duration are recorded

Failed validations are logged and files are marked as failed for retry.

---

## Project Structure

```
python-audio-converter/
├── convert.py              # Main conversion script
├── retry_failed.py         # Retry failed conversions
├── run.sh                  # Convenience wrapper (edit paths here)
├── requirements.txt        # Python dependencies (none needed!)
├── README.md              # This file
├── .venv/                 # Python virtual environment (optional)
├── .gitignore             # Git ignore rules
└── logs/                  # Auto-created during conversion
    ├── conversion_2026-04-25_14-30-00.log
    ├── manifest_2026-04-25_14-30-00.csv
    └── failed_files.txt
```

---

## Running the Conversion

### Method 1: Using `run.sh` (Recommended)

Edit `run.sh` first to set your source and output directories:

```bash
SOURCE_DIR="./wma"     # Change this to your .wma location
OUTPUT_DIR="./mp3"     # Change this to your desired output
```

Then run:

```bash
# Preview without converting
./run.sh dry-run

# Convert with confirmation prompt
./run.sh convert

# Convert without confirmation
./run.sh convert-yes

# Use CBR 320k instead of VBR
./run.sh cbr320
```

### Method 2: Direct Python Invocation

```bash
# Basic usage
python3 convert.py <source_dir> <output_dir>

# Dry run first (recommended)
python3 convert.py ./wma ./mp3 --dry-run

# Convert with confirmation
python3 convert.py ./wma ./mp3

# Skip confirmation prompt
python3 convert.py ./wma ./mp3 --yes

# Use CBR 320k instead of VBR
python3 convert.py ./wma ./mp3 --bitrate 320k

# Custom VBR quality (0=best, 9=worst)
python3 convert.py ./wma ./mp3 --quality 2
```

### What Happens During Conversion

1. **Preflight Checks**
   - Verifies FFmpeg is installed with libmp3lame
   - Checks source directory exists
   - Creates output directory if needed

2. **File Discovery**
   - Recursively scans for `.wma` and `.WMA` files
   - Displays total file count

3. **Space Estimation**
   - Shows total source size
   - Estimates output size (~90% of source)
   - Warns if disk space is tight

4. **Confirmation** (unless `--yes` flag used)
   - Shows file count and estimated size
   - Prompts: "Proceed with conversion? [y/N]"

5. **Conversion**
   - Processes files sequentially (single-threaded)
   - Shows progress: `[45/200]` format
   - Validates each output file
   - Logs successes and failures

6. **Reporting**
   - Creates detailed CSV manifest (`logs/manifest_*.csv`)
   - Creates failed files list (`logs/failed_files.txt`)
   - Displays summary with totals, duration, compression ratio

---

## Retrying Failed Conversions

If any files fail during conversion, they're logged to `logs/failed_files.txt`.

### Method 1: Using `retry_failed.py` (Recommended)

```bash
# Retry with confirmation
python3 retry_failed.py ./mp3

# Retry without confirmation
python3 retry_failed.py ./mp3 --yes

# Retry with CBR 320k
python3 retry_failed.py ./mp3 --bitrate 320k
```

Or use the wrapper:

```bash
./run.sh retry
```

### Method 2: Manual Retry of Specific Files

If you need to manually retry specific files:

```bash
# Convert a single file
python3 -c "
from convert import convert_file, setup_logging
from pathlib import Path

logger = setup_logging(Path('logs'))
result = convert_file(
    Path('/path/to/file.wma'),
    Path('/source/dir'),
    Path('/output/dir'),
    '0',  # VBR quality
    None,  # CBR bitrate
    logger,
    False  # not dry-run
)
print(result)
"
```

---

## Verifying Results

### 1. Check Conversion Summary

The script outputs a summary showing:
- Total files processed
- Successes vs. failures
- Total size before/after
- Compression ratio

### 2. Review the CSV Manifest

Open `logs/manifest_*.csv` to see detailed stats for every file:

| Status | Source File | Output File | Size Before | Size After | Compression % | Bitrate | Duration | Conversion Time | Error |
|--------|-------------|-------------|-------------|------------|---------------|---------|----------|-----------------|-------|
| SUCCESS | song.wma | song.mp3 | 5242880 | 4718592 | 90.0 | 256000 | 180.5 | 2.34 | |

### 3. Spot-Check via FFprobe

```bash
# Inspect metadata on a converted file
ffprobe -v quiet -print_format json -show_format "./mp3/Artist/song.mp3"
```

### 4. Check Cover Art Was Preserved

```bash
ffprobe -v quiet -show_streams "./mp3/Artist/song.mp3" | grep codec_name
# Should output: mp3 and mjpeg (mjpeg = embedded cover art)
```

### 5. Verify File Count Matches

```bash
# Count source WMA files
find ./wma -name "*.wma" -o -name "*.WMA" | wc -l

# Count output MP3 files
find ./mp3 -name "*.mp3" | wc -l
```

Both counts should match. Any discrepancy means failed conversions — check
`logs/failed_files.txt`.

### 6. Recommended: Validate with MusicBrainz Picard

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
chmod -R u+w ./mp3
```

### Cover art missing after conversion

This should not happen with the updated script, which uses `-map 0:v? -c:v copy`.

If you still have issues, check the source WMA file has embedded art:

```bash
ffprobe -v quiet -show_streams input.wma | grep codec_name
# Should show: wmav2 and mjpeg (or similar video codec)
```

If no video stream is present in the source, there's no cover art to copy.

### Conversion is slow

- The script is intentionally single-threaded for safety and reliability
- M-series Macs should convert at roughly 10-20x real-time speed
- For a 2000-file library, expect 15-45 minutes depending on file sizes

### A file fails with `Invalid data found`

The source WMA file may be corrupt. Verify with:

```bash
ffprobe -v error "path/to/file.wma"
```

### Special characters in filenames cause errors

The script handles unicode and special characters correctly. If you encounter issues:

```bash
# Check the actual filename encoding
ls -lb "path/to/file.wma"

# If needed, rename files to ASCII-safe names first
# (or file a bug report!)
```

### Python module import errors

Make sure you're using Python 3.9.6+:

```bash
python3 --version
```

All required modules are in the standard library. No `pip install` needed.

---

## FAQ

**Q: Will this delete my original WMA files?**
No. The script writes all output to a separate `output_dir`. Source files are never modified
or deleted. Originals remain untouched.

**Q: What happens if an MP3 already exists at the output path?**
FFmpeg will overwrite it (the `-y` flag). The script always re-converts files, ensuring
any quality settings changes are applied.

**Q: Can I convert to a different format (AAC, FLAC, etc.)?**
Yes — modify `convert.py`:
- Change `-codec:a libmp3lame` to `-codec:a aac` (AAC) or `-codec:a flac` (lossless FLAC)
- Update the output suffix in `resolve_output_path()` from `.mp3` to `.m4a` or `.flac`

**Q: How long will 2,000 files take?**
On an M-series Mac, expect approximately **15–45 minutes** depending on file sizes,
average source bitrate, and CPU model. Single-threaded conversion is slower but more
reliable than parallel processing.

**Q: Is VBR MP3 compatible with all players?**
Yes. LAME VBR has been universally supported by iTunes/Music, Spotify, VLC, and
virtually all hardware players since the early 2000s.

**Q: Why single-threaded instead of parallel?**
Single-threaded conversion is:
- More reliable (fewer race conditions)
- Easier to debug (sequential logs)
- Simpler for validation
- Still plenty fast for most use cases

If you need parallel processing, you can modify the script to use `ThreadPoolExecutor`
(see the git history for the original parallel version).

**Q: Can I skip already-converted files to resume a stopped conversion?**
Currently, the script always overwrites existing files. To implement resume capability,
you could add a check in `convert_file()` to skip if the output file already exists
and passes validation.

**Q: Does this work on Linux or Windows?**
The script should work on Linux with minimal changes. On Windows, you'll need to:
- Install Python and FFmpeg for Windows
- Adjust path handling (use `pathlib` which is cross-platform)
- Modify `run.sh` or create a `run.bat` equivalent

**Q: What if I have thousands of files in deeply nested directories?**
No problem. The script uses `Path.rglob()` which handles arbitrary directory depth
and thousands of files efficiently.

---

## License

This project is provided as-is for personal and educational use.

---

## Support

For issues, questions, or suggestions:
- Check the [Troubleshooting](#troubleshooting) section
- Review `logs/conversion_*.log` for detailed error messages
- Inspect `logs/failed_files.txt` for failed conversions
- Examine `logs/manifest_*.csv` for per-file statistics

---

**Happy Converting! 🎵**
