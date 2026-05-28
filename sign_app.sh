#!/bin/bash
# Inside-out code-sign the PyInstaller .app with a stable self-signed cert so
# macOS TCC anchors Screen Recording / Camera grants to the certificate
# Authority (which survives rebuilds), instead of the per-build cdhash.
#
# Prereq (one-time, GUI): create a self-signed Code Signing certificate in
# Keychain Access named exactly $CERT (default "LocalAppDev"), valid for years,
# and set its Trust -> Code Signing to "Always Trust". See CLAUDE.md.
#
# Usage:  ./sign_app.sh [path/to/ScreenCapture.app]
set -euo pipefail

CERT="${CERT:-LocalAppDev}"
APP_PATH="${1:-dist/ScreenCapture.app}"
ENTITLEMENTS="$(cd "$(dirname "$0")" && pwd)/entitlements.plist"

if ! security find-identity -v -p codesigning | grep -q "$CERT"; then
  echo "ERROR: code-signing identity '$CERT' not found in the keychain."
  echo "Create it in Keychain Access (Certificate Assistant -> Create a"
  echo "Certificate; Self-Signed Root; Code Signing) and set it to Always Trust."
  exit 1
fi
if [ ! -d "$APP_PATH" ]; then
  echo "ERROR: app bundle not found at $APP_PATH (build it first: pyinstaller ScreenCapture.spec)"
  exit 1
fi

echo "Signing $APP_PATH with identity '$CERT' (inside-out, no --deep)..."

# 1. Strip any stale ad-hoc/broken signatures from inner files.
find "$APP_PATH" -type f -print0 | xargs -0 codesign --remove-signature 2>/dev/null || true

# 2. Sign every compiled extension / dylib first.
find "$APP_PATH" -type f \( -name "*.so" -o -name "*.dylib" \) -print0 \
  | while IFS= read -r -d '' f; do
      codesign --force --timestamp=none --sign "$CERT" --options runtime "$f"
    done

# 3. Sign nested .framework bundles (PyQt6, Python core, etc.).
if [ -d "$APP_PATH/Contents/Frameworks" ]; then
  find "$APP_PATH/Contents/Frameworks" -type d -name "*.framework" -print0 \
    | while IFS= read -r -d '' fw; do
        codesign --force --timestamp=none --sign "$CERT" --options runtime "$fw"
      done
fi

# 4. Sign any embedded python interpreter binaries (the TCC "responsible process").
find "$APP_PATH/Contents" -type f -name "python*" -perm -111 -print0 2>/dev/null \
  | while IFS= read -r -d '' py; do
      codesign --force --timestamp=none --sign "$CERT" \
        --entitlements "$ENTITLEMENTS" --options runtime "$py"
    done

# 5. Sign the main bootloader executable.
codesign --force --timestamp=none --sign "$CERT" \
  --entitlements "$ENTITLEMENTS" --options runtime \
  "$APP_PATH/Contents/MacOS/ScreenCapture"

# 6. Finally, sign the outer .app wrapper.
codesign --force --timestamp=none --sign "$CERT" \
  --entitlements "$ENTITLEMENTS" --options runtime "$APP_PATH"

echo "--- verification ---"
codesign --verify --deep --strict --verbose=2 "$APP_PATH" && echo "signature OK"
echo "--- authority / entitlements ---"
codesign -dvvv "$APP_PATH" 2>&1 | grep -E "Identifier|Authority|TeamIdentifier" || true
echo
echo "Done. Move the app to ~/Applications, then (first time only):"
echo "  tccutil reset ScreenCapture com.screencapture.app"
echo "  tccutil reset Camera        com.screencapture.app"
echo "Launch it, grant Screen Recording + Camera once — it should now show as"
echo "'ScreenCapture' and the grant will persist across future rebuilds."
