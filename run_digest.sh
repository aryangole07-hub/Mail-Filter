#!/bin/bash
# run_digest.sh - the macOS / Linux counterpart of run_digest.ps1.
#
# Run by the com.mailfilter.digest LaunchAgent at 07:55, and by the Refresh
# button. It does what a bare "python mail_filter.py" gets wrong when nobody is
# at the keyboard:
#   * runs from the app folder with the app's own Python
#   * forces UTF-8 and marks the run non-interactive
#   * starts Ollama if the Mac booted without it
#   * rotates digest.log at 5 MB
#
# By hand:  ./run_digest.sh --hours 48

set -u
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR" || exit 1

PY="$DIR/.venv/bin/python"
LOG="$DIR/digest.log"

if [ ! -x "$PY" ]; then
    echo "ERROR: the app's Python is missing at $PY - run the installer again." >> "$LOG"
    exit 1
fi

if [ -f "$LOG" ] && [ "$(wc -c < "$LOG")" -gt 5242880 ]; then
    mv -f "$LOG" "$LOG.1"
fi

export PYTHONUTF8=1
export MAIL_FILTER_NONINTERACTIVE=1

ollama_up() { curl -s -m 5 http://localhost:11434/api/tags > /dev/null 2>&1; }

if ! ollama_up; then
    if [ -d "/Applications/Ollama.app" ]; then
        open -g -a Ollama
    elif command -v ollama > /dev/null 2>&1; then
        nohup ollama serve > /dev/null 2>&1 &
    fi
    for _ in $(seq 1 30); do
        sleep 1
        ollama_up && break
    done
fi

{
    echo ""
    echo "===== run at $(date '+%Y-%m-%d %H:%M:%S') ====="
} >> "$LOG"

"$PY" mail_filter.py "$@" >> "$LOG" 2>&1
code=$?
if [ "$code" -ne 0 ]; then
    echo "(mail_filter.py exited with code $code)" >> "$LOG"
fi
exit "$code"
