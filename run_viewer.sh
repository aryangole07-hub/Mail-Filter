#!/bin/bash
# run_viewer.sh - the macOS / Linux counterpart of run_viewer.ps1.
#
# Starts the local server if it is not already running, waits for it, and opens
# the site as an app window (Chrome, Edge or Brave in --app mode, or the default
# browser). Run at login by the com.mailfilter.viewer LaunchAgent.
#
#   ./run_viewer.sh              start the server and open the window
#   ./run_viewer.sh --no-window  server only
#   ./run_viewer.sh --quiet      log only, no terminal output

set -u
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR" || exit 1

URL="http://127.0.0.1:8765/"
PY="$DIR/.venv/bin/python"
LOG="$DIR/viewer.log"
QUIET=0
WINDOW=1
for arg in "$@"; do
    case "$arg" in
        --quiet) QUIET=1 ;;
        --no-window) WINDOW=0 ;;
    esac
done

note() {
    echo "$(date '+%Y-%m-%d %H:%M:%S')  $*" >> "$LOG"
    [ "$QUIET" -eq 1 ] || echo "$*"
}
port_up() { curl -s -m 2 -o /dev/null "$URL"; }

if port_up; then
    note "Viewer already running on $URL"
else
    if [ ! -x "$PY" ]; then
        note "ERROR: the app's Python is missing at $PY - run the installer again."
        exit 1
    fi
    if [ ! -f "$DIR/digest_store.json" ]; then
        note "No digest yet - nothing to show. Skipping."
        exit 0
    fi
    PYTHONUTF8=1 nohup "$PY" viewer.py --no-browser > /dev/null 2>&1 &
    for _ in $(seq 1 40); do
        sleep 0.5
        port_up && break
    done
    if ! port_up; then
        note "ERROR: viewer did not come up within 20 seconds."
        exit 1
    fi
    note "Viewer started on $URL"
fi

[ "$WINDOW" -eq 1 ] || exit 0

# Already open? Do not stack a second window.
if pgrep -f -- "--app=$URL" > /dev/null 2>&1; then
    note "Window already open."
    exit 0
fi

"$PY" platforms.py open-window "$URL"
note "Opened the app window."
