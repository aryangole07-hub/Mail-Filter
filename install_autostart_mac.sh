#!/bin/bash
# install_autostart_mac.sh - the macOS counterpart of install_autostart.ps1.
#
# Sets up three LaunchAgents for the current user (no admin password):
#   com.mailfilter.digest   daily digest at 07:55
#   com.mailfilter.alert    evening reminder at 20:00
#   com.mailfilter.viewer   opens the app window 30 seconds after login
#
#   ./install_autostart_mac.sh              install / refresh
#   ./install_autostart_mac.sh --uninstall  remove all three

set -u
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR" || exit 1

PY="$DIR/.venv/bin/python"
if [ ! -x "$PY" ]; then
    echo "The app's Python is missing at $PY - run the installer first."
    exit 1
fi

chmod +x "$DIR"/*.sh 2> /dev/null

if [ "${1:-}" = "--uninstall" ]; then
    "$PY" platforms.py uninstall-launch-agents
    echo "Done. Mail Filter will no longer run by itself."
    exit 0
fi

"$PY" platforms.py install-launch-agents "$DIR"
echo "Done. Mail Filter opens by itself when you log in, prepares a digest at 7:55"
echo "and reminds you about tomorrow at 8 pm."
