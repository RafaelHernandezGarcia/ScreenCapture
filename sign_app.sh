#!/bin/bash
# Inside-out code-sign the PyInstaller .app with a stable self-signed cert so
# macOS TCC anchors Screen Recording / Camera grants to the certificate
# Authority (which survives rebuilds), instead of the per-build cdhash.
#
# Prereq (one-time, GUI): a self-signed Code Signing certificate named exactly
# $CERT (default "LocalAppDev") in the keychain, trusted for code signing.
# See CLAUDE.md.
#
# Usage:  ./sign_app.sh [path/to/ScreenCapture.app]
set -uo pipefail   # NOT -e: a single inner failure must not abort the loop

CERT="${CERT:-LocalAppDev}"
APP_PATH="${1:-dist/ScreenCapture.app}"
ENTITLEMENTS="$(cd "$(dirname "$0")" && pwd)/entitlements.plist"
MAIN_EXE="$APP_PATH/Contents/MacOS/ScreenCapture"

if ! security find-identity -v -p codesigning | grep -q "$CERT"; then
  echo "ERROR: code-signing identity '$CERT' not found / not trusted in the keychain."
  exit 1
fi
if [ ! -d "$APP_PATH" ]; then
  echo "ERROR: app bundle not found at $APP_PATH (build it first)."
  exit 1
fi

echo "Signing $APP_PATH with identity '$CERT' (inside-out, no --deep)..."

# 1. Strip stale ad-hoc/broken signatures.
find "$APP_PATH" -type f -print0 | xargs -0 codesign --remove-signature 2>/dev/null || true

# 2. Sign EVERY nested Mach-O binary (extensions, dylibs, and standalone
#    executables like ffmpeg / sc_audio_helper / embedded python) — but NOT
#    the bundle's own main executable (that gets signed by signing the .app).
warns=0
while IFS= read -r -d '' f; do
  [ "$f" = "$MAIN_EXE" ] && continue
  if file -b "$f" | grep -q "Mach-O"; then
    codesign --force --timestamp=none --sign "$CERT" --options runtime "$f" 2>/dev/null \
      || { echo "  warn: could not sign ${f#$APP_PATH/}"; warns=$((warns+1)); }
  fi
done < <(find "$APP_PATH" -type f -print0)
[ "$warns" -gt 0 ] && echo "  ($warns inner files could not be signed)"

# 3. Sign the .app bundle itself with entitlements. This signs the main
#    executable (applying the entitlements) and seals the bundle.
codesign --force --timestamp=none --sign "$CERT" \
  --entitlements "$ENTITLEMENTS" --options runtime "$APP_PATH"

echo "--- verification ---"
if codesign --verify --deep --strict --verbose=2 "$APP_PATH"; then
  echo "signature OK"
else
  echo "WARNING: verification reported issues (see above)"
fi
echo "--- authority / entitlements ---"
codesign -dvv "$APP_PATH" 2>&1 | grep -E "Identifier|Authority" || true
