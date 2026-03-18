#!/bin/bash
# Sync development source → macOS app bundle (run after making changes)
SRC="${1:-$HOME/Downloads/ScreenCapture}"
APP="/Applications/ScreenCapture.app/Contents/Resources/app"

if [[ ! -d "$APP" ]]; then
  echo "App bundle not found at $APP"
  echo "Run install.sh first, or pass source dir: $0 /path/to/ScreenCapture"
  exit 1
fi

echo "Syncing $SRC → app bundle..."
osascript -e 'quit app "ScreenCapture"' 2>/dev/null
sleep 2
pkill -f "ScreenCapture.app" 2>/dev/null

cp "$SRC"/*.py "$APP/"
cp "$SRC/sc_audio_helper" "$APP/" 2>/dev/null
cp "$SRC/requirements.txt" "$APP/"
cp -r "$SRC/assets/" "$APP/assets/" 2>/dev/null

"$APP/.venv/bin/pip" install -r "$APP/requirements.txt" --quiet 2>/dev/null

echo "Done. Relaunch: open /Applications/ScreenCapture.app"
