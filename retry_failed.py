#!/usr/bin/env python3
"""
Retry Failed WMA Conversions
=============================
Re-processes files that failed during a previous conversion run.

Reads logs/failed_files.txt and attempts to convert only those specific files.

Usage:
    python3 retry_failed.py <output_dir> [options]

Examples:
    python3 retry_failed.py ./mp3
    python3 retry_failed.py ./mp3 --yes
    python3 retry_failed.py ./mp3 --bitrate 320k
"""

import argparse
import sys
from pathlib import Path

# Import from convert.py
from convert import (
    convert_file,
    setup_logging,
    check_ffmpeg,
    write_failed_report,
    write_manifest,
    print_summary,
    confirm_conversion,
    estimate_total_size,
    check_available_space,
    DEFAULT_VBR_QUALITY,
    LOG_DIR,
)
from datetime import datetime


def load_failed_files(failed_list_path: Path) -> list:
    """Load list of failed files from logs/failed_files.txt"""
    if not failed_list_path.exists():
        print(f"ERROR: Failed files list not found: {failed_list_path}")
        print("Run a conversion first to generate failed_files.txt")
        sys.exit(1)

    with open(failed_list_path, "r", encoding="utf-8") as f:
        files = [Path(line.strip()) for line in f if line.strip()]

    # Filter out files that don't exist
    existing_files = [f for f in files if f.exists()]

    if len(existing_files) < len(files):
        missing_count = len(files) - len(existing_files)
        print(f"WARNING: {missing_count} file(s) from failed list no longer exist")

    return existing_files


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Retry conversion of files that failed previously.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 retry_failed.py ./mp3
  python3 retry_failed.py ./mp3 --yes
  python3 retry_failed.py ./mp3 --bitrate 320k
        """,
    )
    parser.add_argument("output_dir", type=Path, help="Directory to write .mp3 files")
    parser.add_argument(
        "--quality", type=str, default=DEFAULT_VBR_QUALITY,
        help="LAME VBR quality 0–9 (0=best, default: 0). Ignored if --bitrate is set."
    )
    parser.add_argument(
        "--bitrate", type=str, default=None,
        help="CBR bitrate, e.g. 320k. Overrides --quality."
    )
    parser.add_argument(
        "--yes", "-y", action="store_true",
        help="Skip confirmation prompt and start conversion immediately."
    )
    parser.add_argument(
        "--failed-list", type=Path, default=Path(LOG_DIR) / "failed_files.txt",
        help="Path to failed files list (default: logs/failed_files.txt)"
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    output_dir: Path = args.output_dir.expanduser().resolve()
    log_dir: Path = Path(LOG_DIR)
    failed_list_path: Path = args.failed_list.expanduser().resolve()

    # ── Preflight checks ───────────────────────────────────────────────────────
    check_ffmpeg()

    logger = setup_logging(log_dir)

    # ── Load failed files ──────────────────────────────────────────────────────
    logger.info(f"Loading failed files from: {failed_list_path}")
    failed_files = load_failed_files(failed_list_path)

    if not failed_files:
        logger.info("No failed files to retry. Exiting.")
        return

    quality_label = (
        f"CBR {args.bitrate}" if args.bitrate else f"VBR q:a {args.quality}"
    )

    logger.info(f"Found {len(failed_files)} failed file(s) to retry")
    logger.info(f"Output : {output_dir}")
    logger.info(f"Quality: {quality_label}")

    # ── Estimate size and check space ──────────────────────────────────────────
    estimated_size = estimate_total_size(failed_files, logger)
    check_available_space(output_dir, estimated_size, logger)

    # ── Confirmation ───────────────────────────────────────────────────────────
    if not args.yes:
        if not confirm_conversion(len(failed_files), estimated_size):
            logger.info("Retry cancelled by user.")
            return

    # ── Retry conversions ──────────────────────────────────────────────────────
    results = []
    start_time = datetime.now()

    for idx, wma_file in enumerate(failed_files, 1):
        logger.info(f"[{idx}/{len(failed_files)}] Retrying: {wma_file.name}")

        # Determine source directory from the failed file's parent
        # We need to reconstruct the source_dir to maintain directory structure
        # For simplicity, we'll use the file's parent as source_dir
        source_dir = wma_file.parent

        result = convert_file(
            wma_file,
            source_dir,
            output_dir / wma_file.parent.name,  # Maintain some structure
            args.quality,
            args.bitrate,
            logger,
            dry_run=False,
        )
        results.append(result)

        status = "✓" if result["success"] else "✗"
        logger.info(f"[{idx}/{len(failed_files)}] {status} {result['file'].name}")

    elapsed = (datetime.now() - start_time).total_seconds()

    # ── Report ─────────────────────────────────────────────────────────────────
    write_manifest(results, log_dir)

    # Write updated failed list (only files that failed again)
    still_failed = [r for r in results if not r["success"]]
    if still_failed:
        write_failed_report(still_failed, log_dir)
        logger.info(f"{len(still_failed)} file(s) still failed after retry")
    else:
        logger.info("All files successfully converted!")

    print_summary(results, elapsed)


if __name__ == "__main__":
    main()
