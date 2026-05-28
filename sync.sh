#!/bin/bash
# Sync development source → macOS app bundle (run after making changes)
SRC="${1:-$HOME/Documents/ScreenCapture}"

# Prefer ~/Applications (install.sh default) then /Applications
if [[ -d "$HOME/Applications/ScreenCapture" ]]; then
  APP="$HOME/Applications/ScreenCapture"
  APP_OPEN="$HOME/Applications/ScreenCapture.app"
elif [[ -d "/Applications/ScreenCapture.app/Contents/Resources/app" ]]; then
  APP="/Applications/ScreenCapture.app/Contents/Resources/app"
  APP_OPEN="/Applications/ScreenCapture.app"
else
  echo "App not found. Run install.sh first from project dir."
  echo "  cd $SRC && ./install.sh"
  echo "Or pass source: $0 /path/to/ScreenCapture"
  exit 1
fi

echo "Syncing $SRC → $APP ..."
osascript -e 'quit app "ScreenCapture"' 2>/dev/null
sleep 2
pkill -f "ScreenCapture.app" 2>/dev/null

cp "$SRC"/*.py "$APP/"
cp "$SRC/sc_audio_helper" "$APP/" 2>/dev/null
cp "$SRC/requirements.txt" "$APP/"
cp -r "$SRC/assets/" "$APP/assets/" 2>/dev/null

"$APP/.venv/bin/pip" install -r "$APP/requirements.txt" --quiet 2>/dev/null

echo "Done. Relaunch: open $APP_OPEN"
