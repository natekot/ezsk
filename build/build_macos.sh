#!/usr/bin/env bash
set -euo pipefail

# Build ezsk.app for macOS
#
# Prerequisites:
#   pip install pyinstaller
#   pip install ezsk[web,verify]
#
# Usage:
#   ./build/build_macos.sh
#
# Output:
#   dist/ezsk.app   — standalone macOS application
#   dist/ezsk/      — onedir bundle (intermediate)

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

echo "=== Building ezsk for macOS ==="
echo "Root: $ROOT"

cd "$ROOT"

# Ensure PyInstaller is available
if ! command -v pyinstaller &>/dev/null; then
    echo "Installing PyInstaller..."
    pip install pyinstaller
fi

# Clean previous builds
rm -rf dist/ezsk dist/ezsk.app build/__pycache__

# Build
echo "Running PyInstaller..."
pyinstaller build/ezsk.spec --noconfirm

# Download slskd binary and include it in the bundle
echo "Downloading slskd binary for bundling..."
python3 -c "
from ezsk.slskd_manager import download_slskd, _slskd_binary_path
binary = download_slskd(verbose=True)
print(f'slskd binary: {binary}')
"

SLSKD_BIN="$(python3 -c "from ezsk.slskd_manager import _slskd_binary_path; print(_slskd_binary_path())")"
if [ -f "$SLSKD_BIN" ]; then
    echo "Bundling slskd binary..."
    if [ -d "dist/ezsk.app" ]; then
        # .app bundle
        RESOURCES="dist/ezsk.app/Contents/Resources"
        mkdir -p "$RESOURCES/bin"
        cp "$SLSKD_BIN" "$RESOURCES/bin/slskd"
        chmod +x "$RESOURCES/bin/slskd"
    fi
    if [ -d "dist/ezsk" ]; then
        # onedir bundle
        mkdir -p "dist/ezsk/bin"
        cp "$SLSKD_BIN" "dist/ezsk/bin/slskd"
        chmod +x "dist/ezsk/bin/slskd"
    fi
else
    echo "WARNING: slskd binary not found at $SLSKD_BIN, skipping bundle"
fi

# Summary
echo ""
echo "=== Build complete ==="
if [ -d "dist/ezsk.app" ]; then
    APP_SIZE=$(du -sh "dist/ezsk.app" | cut -f1)
    echo "  dist/ezsk.app  ($APP_SIZE)"
fi
if [ -d "dist/ezsk" ]; then
    DIR_SIZE=$(du -sh "dist/ezsk" | cut -f1)
    echo "  dist/ezsk/     ($DIR_SIZE)"
fi

echo ""
echo "To create a DMG:"
echo "  hdiutil create -volname ezsk -srcfolder dist/ezsk.app -ov dist/ezsk.dmg"
echo ""
echo "To code sign (requires Apple Developer certificate):"
echo "  codesign --deep --force --sign \"Developer ID Application: YOUR NAME\" dist/ezsk.app"
echo "  xcrun notarytool submit dist/ezsk.dmg --apple-id YOUR_ID --team-id YOUR_TEAM"
