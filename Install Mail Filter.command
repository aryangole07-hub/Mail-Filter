#!/bin/bash
# Install Mail Filter.command - double-click this on a Mac to install.
#
# It finds a Python 3.10+ that has the window toolkit the installer needs, or
# opens Apple-friendly Python installer from python.org if there is none, and
# then starts the same setup wizard Windows users get.

cd "$(dirname "$0")" || exit 1
clear
echo "Mail Filter setup"
echo "================="
echo

usable() {
    "$1" -c 'import sys, tkinter; sys.exit(0 if sys.version_info >= (3, 10) else 1)' > /dev/null 2>&1
}

PY=""
for candidate in \
    /Library/Frameworks/Python.framework/Versions/3.13/bin/python3 \
    /Library/Frameworks/Python.framework/Versions/3.12/bin/python3 \
    /Library/Frameworks/Python.framework/Versions/3.11/bin/python3 \
    /opt/homebrew/bin/python3 \
    /usr/local/bin/python3 \
    python3; do
    if command -v "$candidate" > /dev/null 2>&1 && usable "$candidate"; then
        PY="$candidate"
        break
    fi
done

if [ -z "$PY" ]; then
    echo "Python is needed first. Downloading the official installer from python.org..."
    PKG="/tmp/python-3.12.10-macos11.pkg"
    if curl -L --fail -o "$PKG" "https://www.python.org/ftp/python/3.12.10/python-3.12.10-macos11.pkg"; then
        echo
        echo "The Python installer is opening. Click Continue / Agree / Install in it."
        echo "When it says the installation was successful, double-click"
        echo "\"Install Mail Filter\" again."
        open "$PKG"
    else
        echo "Could not download Python. Check the internet connection and try again."
    fi
    echo
    read -r -p "Press Return to close this window." _
    exit 0
fi

echo "Starting the installer window..."
"$PY" setup_wizard.py
