#!/bin/bash
# run_alert.sh - the macOS / Linux counterpart of run_alert.ps1.
# Run by the com.mailfilter.alert LaunchAgent at 20:00.
# By hand, to see tomorrow's agenda:  ./run_alert.sh --print

set -u
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR" || exit 1

PY="$DIR/.venv/bin/python"
[ -x "$PY" ] || exit 1

export PYTHONUTF8=1
exec "$PY" alerts.py "$@"
