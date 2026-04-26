#!/bin/bash
#
# WMA to MP3 Conversion - Convenience Wrapper
# ============================================
# Quick commands for common conversion scenarios.
#
# Edit the SOURCE_DIR and OUTPUT_DIR variables below to match your setup.
#

set -euo pipefail

# ── Configuration ──────────────────────────────────────────────────────────────
# EDIT THESE PATHS TO MATCH YOUR SETUP:

SOURCE_DIR="./input"   # Directory containing your source audio files
OUTPUT_DIR=""          # Output directory (leave empty to use ./output default)

# ───────────────────────────────────────────────────────────────────────────────

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONVERT_PY="${SCRIPT_DIR}/convert.py"
RETRY_PY="${SCRIPT_DIR}/retry_failed.py"
VENV_ACTIVATE="${SCRIPT_DIR}/.venv/bin/activate"

# Activate virtual environment if it exists
if [[ -f "${VENV_ACTIVATE}" ]]; then
    source "${VENV_ACTIVATE}"
fi

# Color output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# ── Helper Functions ───────────────────────────────────────────────────────────

usage() {
    local output_label="${OUTPUT_DIR:-./output (default)}"
    cat << EOF
${BLUE}WMA to MP3 Conversion Wrapper${NC}

Usage: $0 [command]

Commands:
    ${GREEN}dry-run${NC}     Preview what will be converted (recommended first step)
    ${GREEN}convert${NC}     Convert all .wma files with confirmation prompt
    ${GREEN}convert-yes${NC} Convert all .wma files without prompting (auto-confirm)
    ${GREEN}resume${NC}      Resume a previously interrupted conversion
    ${GREEN}retry${NC}       Retry previously failed conversions
    ${GREEN}cbr320${NC}      Convert using CBR 320k instead of VBR (with prompt)
    ${GREEN}help${NC}        Show this help message

Configuration (edit this file to change):
    Source: ${SOURCE_DIR}
    Output: ${output_label}

Examples:
    $0 dry-run        # Always run this first!
    $0 convert        # Convert with confirmation
    $0 convert-yes    # Convert without confirmation
    $0 retry          # Retry failed files

EOF
}

check_python() {
    if ! command -v python3 &> /dev/null; then
        echo -e "${RED}ERROR: python3 not found${NC}"
        echo "Install Python 3.9.6+ first"
        exit 1
    fi
}

check_ffmpeg() {
    if ! command -v ffmpeg &> /dev/null; then
        echo -e "${RED}ERROR: ffmpeg not found${NC}"
        echo "Install with: brew install ffmpeg"
        exit 1
    fi
}

check_script() {
    if [[ ! -f "${CONVERT_PY}" ]]; then
        echo -e "${RED}ERROR: convert.py not found${NC}"
        echo "Expected at: ${CONVERT_PY}"
        exit 1
    fi
}

# ── Commands ───────────────────────────────────────────────────────────────────

build_output_args() {
    # Return output dir arg only if OUTPUT_DIR is set; script uses ./output by default
    if [[ -n "${OUTPUT_DIR}" ]]; then
        echo "${OUTPUT_DIR}"
    fi
}

cmd_dry_run() {
    echo -e "${BLUE}Running dry-run...${NC}"
    python3 "${CONVERT_PY}" "${SOURCE_DIR}" $(build_output_args) --dry-run
}

cmd_convert() {
    echo -e "${BLUE}Starting conversion with confirmation prompt...${NC}"
    python3 "${CONVERT_PY}" "${SOURCE_DIR}" $(build_output_args)
}

cmd_convert_yes() {
    echo -e "${YELLOW}Starting conversion without confirmation...${NC}"
    python3 "${CONVERT_PY}" "${SOURCE_DIR}" $(build_output_args) --yes
}

cmd_resume() {
    echo -e "${BLUE}Resuming interrupted conversion...${NC}"
    python3 "${CONVERT_PY}" "${SOURCE_DIR}" $(build_output_args) --resume
}

cmd_retry() {
    echo -e "${BLUE}Retrying failed conversions...${NC}"
    if [[ -f "${SCRIPT_DIR}/logs/failed_files.txt" ]]; then
        local output_dir="${OUTPUT_DIR:-${SCRIPT_DIR}/output}"
        python3 "${RETRY_PY}" "${output_dir}"
    else
        echo -e "${RED}ERROR: No failed_files.txt found${NC}"
        echo "Run a conversion first to generate this file"
        exit 1
    fi
}

cmd_cbr320() {
    echo -e "${BLUE}Converting with CBR 320k...${NC}"
    python3 "${CONVERT_PY}" "${SOURCE_DIR}" $(build_output_args) --bitrate 320k
}

# ── Main ───────────────────────────────────────────────────────────────────────

main() {
    check_python
    check_ffmpeg
    check_script

    local command="${1:-help}"

    case "$command" in
        dry-run)
            cmd_dry_run
            ;;
        convert)
            cmd_convert
            ;;
        resume)
            cmd_resume
            ;;
        convert-yes)
            cmd_convert_yes
            ;;
        retry)
            cmd_retry
            ;;
        cbr320)
            cmd_cbr320
            ;;
        help|--help|-h)
            usage
            ;;
        *)
            echo -e "${RED}ERROR: Unknown command '$command'${NC}\n"
            usage
            exit 1
            ;;
    esac
}

main "$@"
