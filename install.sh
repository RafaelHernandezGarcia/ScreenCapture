#!/bin/bash
set -e

echo "========================================"
echo "  ScreenCapture Installation"
echo "========================================"
echo ""

OS_TYPE="$(uname -s)"

if [ "$OS_TYPE" = "Darwin" ]; then
    # ---- macOS Installation ----
    INSTALL_DIR="$HOME/Applications/ScreenCapture"
    SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

    # Find Python 3
    PYTHON_EXE=""
    for p in python3 python; do
        if command -v "$p" &>/dev/null; then
            PYTHON_EXE="$(command -v "$p")"
            break
        fi
    done

    if [ -z "$PYTHON_EXE" ]; then
        echo "ERROR: Python 3 not found. Please install Python 3."
        echo "  brew install python3"
        exit 1
    fi

    echo "Found Python: $PYTHON_EXE"
    echo "Installing to: $INSTALL_DIR"
    echo ""

    # Copy files
    mkdir -p "$INSTALL_DIR/assets"
    cp "$SCRIPT_DIR"/*.py "$INSTALL_DIR/"
    cp "$SCRIPT_DIR/requirements.txt" "$INSTALL_DIR/"
    if [ -d "$SCRIPT_DIR/assets" ]; then
        cp "$SCRIPT_DIR/assets/"* "$INSTALL_DIR/assets/" 2>/dev/null || true
    fi
    if [ -f "$SCRIPT_DIR/sc_audio_helper" ]; then
        cp "$SCRIPT_DIR/sc_audio_helper" "$INSTALL_DIR/" 2>/dev/null || true
    fi

    # Create virtual environment and install dependencies
    VENV_DIR="$INSTALL_DIR/.venv"
    if [ ! -d "$VENV_DIR" ]; then
        echo "Creating virtual environment..."
        "$PYTHON_EXE" -m venv "$VENV_DIR"
    fi
    VENV_PYTHON="$VENV_DIR/bin/python3"

    echo "Installing Python dependencies..."
    "$VENV_PYTHON" -m pip install -r "$INSTALL_DIR/requirements.txt" --quiet

    # Create launcher script
    LAUNCHER="$INSTALL_DIR/ScreenCapture.command"
    cat > "$LAUNCHER" <<LAUNCHER_EOF
#!/bin/bash
cd "$INSTALL_DIR"
"$VENV_DIR/bin/python3" main.py &
disown
LAUNCHER_EOF
    chmod +x "$LAUNCHER"

    # Create macOS .app bundle for Spotlight / Launchpad
    APP_DIR="$HOME/Applications/ScreenCapture.app"
    mkdir -p "$APP_DIR/Contents/MacOS"
    mkdir -p "$APP_DIR/Contents/Resources"

    # Info.plist
    cat > "$APP_DIR/Contents/Info.plist" <<PLIST_EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key>
    <string>ScreenCapture</string>
    <key>CFBundleDisplayName</key>
    <string>ScreenCapture</string>
    <key>CFBundleIdentifier</key>
    <string>com.screencapture.app</string>
    <key>CFBundleVersion</key>
    <string>1.1</string>
    <key>CFBundleShortVersionString</key>
    <string>1.1</string>
    <key>CFBundleExecutable</key>
    <string>ScreenCapture</string>
    <key>CFBundleIconFile</key>
    <string>icon</string>
    <key>LSUIElement</key>
    <true/>
</dict>
</plist>
PLIST_EOF

    # Executable wrapper (exec -a shows "ScreenCapture" instead of "Python" in Activity Monitor)
    cat > "$APP_DIR/Contents/MacOS/ScreenCapture" <<EXEC_EOF
#!/bin/bash
cd "$INSTALL_DIR"
exec -a "ScreenCapture" "$VENV_DIR/bin/python3" main.py
EXEC_EOF
    chmod +x "$APP_DIR/Contents/MacOS/ScreenCapture"

    # Copy icon if available
    if [ -f "$INSTALL_DIR/assets/icon.png" ]; then
        cp "$INSTALL_DIR/assets/icon.png" "$APP_DIR/Contents/Resources/icon.png"
    fi

    # Create login item (auto-start on boot) via AppleScript
    echo "Setting up auto-start on login..."
    osascript -e "tell application \"System Events\" to make login item at end with properties {path:\"$APP_DIR\", hidden:true}" 2>/dev/null || true

    echo ""
    echo "========================================"
    echo "  Success!"
    echo "========================================"
    echo "1. Search 'ScreenCapture' in Spotlight (Cmd+Space) to open it."
    echo "2. Press PrintScreen to capture a screenshot."
    echo "3. The app will auto-start when you log in."
    echo ""
    echo "NOTE: macOS will ask for Screen Recording permission on first use."
    echo "      Go to System Settings > Privacy & Security > Screen Recording"
    echo "      and enable access for ScreenCapture (or Terminal/Python)."

else
    echo "This install script is for macOS."
    echo "On Windows, please run install.bat instead."
    exit 1
fi
