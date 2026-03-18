#!/bin/bash
echo "Starting ScreenCapture..."
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# Use venv python if installed, otherwise fall back to system python
if [ -f "$SCRIPT_DIR/.venv/bin/python3" ]; then
    "$SCRIPT_DIR/.venv/bin/python3" main.py &
else
    python3 main.py &
fi
disown
