#!/bin/bash
# Full build pipeline: render icon -> PyInstaller onedir -> self-signed sign ->
# install to ~/Applications. Run from the project root.
#
# One-time prereq: create the "LocalAppDev" self-signed Code Signing cert in
# Keychain Access (see CLAUDE.md / sign_app.sh).
set -euo pipefail
cd "$(dirname "$0")"

PY="${PY:-.venv/bin/python}"
DEST="${DEST:-$HOME/Applications/ScreenCapture.app}"

echo "==> 1/4  render icon.icns from assets/icon.png"
ICONSET="$(mktemp -d)/sc.iconset"; mkdir -p "$ICONSET"
for s in 16 32 128 256 512; do
  sips -z $s $s assets/icon.png --out "$ICONSET/icon_${s}x${s}.png" >/dev/null
  d=$((s*2)); sips -z $d $d assets/icon.png --out "$ICONSET/icon_${s}x${s}@2x.png" >/dev/null
done
iconutil -c icns "$ICONSET" -o assets/icon.icns

echo "==> 2/4  PyInstaller onedir build"
rm -rf build dist
"$PY" -m PyInstaller ScreenCapture.spec --noconfirm --clean

echo "==> 3/4  inside-out code-signing"
./sign_app.sh dist/ScreenCapture.app

echo "==> 4/4  install to $DEST"
# quit any running instance + remove old copy
pkill -9 -f "ScreenCapture.app/Contents/MacOS" 2>/dev/null || true
sleep 1
rm -rf "$DEST"
cp -R dist/ScreenCapture.app "$DEST"

echo
echo "Installed to $DEST"
echo "First run only: grant Screen Recording + Camera once (System Settings)."
echo "Open it:  open \"$DEST\""
