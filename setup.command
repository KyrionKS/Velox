#!/bin/bash
# Velox one-time setup. Double-click this file in Finder.
set -e
cd "$(dirname "$0")"

echo "=== Velox setup ==="
echo

if ! command -v python3 >/dev/null 2>&1; then
    echo "✗ python3 was not found."
    echo "  Install it from https://www.python.org/downloads/"
    echo "  or run:  brew install python"
    echo
    read -n 1 -s -r -p "Press any key to close…"
    exit 1
fi

echo "✓ python3: $(python3 --version)"

if command -v ffmpeg >/dev/null 2>&1; then
    echo "✓ ffmpeg:  $(ffmpeg -version | head -1)"
else
    echo "! ffmpeg was not found. Install it with:"
    echo "      brew install ffmpeg"
    echo "  (You can also locate it later from the Velox window.)"
fi
echo

if [ ! -d ".venv" ]; then
    echo "Creating virtual environment (.venv)…"
    python3 -m venv .venv
fi

echo "Installing Velox and its dependencies…"
./.venv/bin/python -m pip install --upgrade pip >/dev/null
./.venv/bin/python -m pip install -e ".[gui]"

echo
echo "✓ Setup complete."
echo "  Start Velox by double-clicking 'Velox.command' (or build 'Velox.app'"
echo "  with 'make_app.command' for a real app icon)."
echo
read -n 1 -s -r -p "Press any key to close…"
