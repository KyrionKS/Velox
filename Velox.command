#!/bin/bash
# Start the Velox GUI. Double-click this file in Finder.
cd "$(dirname "$0")"

if [ ! -x ".venv/bin/python" ]; then
    echo "Velox is not set up yet. Please run 'setup.command' first."
    read -n 1 -s -r -p "Press any key to close…"
    exit 1
fi

exec "./.venv/bin/python" -m velox.gui
