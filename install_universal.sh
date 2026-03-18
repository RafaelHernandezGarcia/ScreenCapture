#!/bin/bash
# Universal installer — detects OS and runs the right installer
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
OS_TYPE="$(uname -s)"

if [ "$OS_TYPE" = "Darwin" ]; then
    echo "Detected macOS — running install.sh"
    bash "$SCRIPT_DIR/install.sh"
elif echo "$OS_TYPE" | grep -qi "MINGW\|MSYS\|CYGWIN"; then
    echo "Detected Windows (Git Bash / MSYS) — running install.bat"
    cmd.exe /c "$SCRIPT_DIR\\install.bat"
else
    # Check if running on Windows via WSL
    if grep -qi microsoft /proc/version 2>/dev/null; then
        echo "Detected WSL — running install.bat via cmd.exe"
        WINPATH=$(wslpath -w "$SCRIPT_DIR/install.bat")
        cmd.exe /c "$WINPATH"
    else
        echo "Unsupported OS: $OS_TYPE"
        echo "Supported: macOS (install.sh) and Windows (install.bat)"
        exit 1
    fi
fi
