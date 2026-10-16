#!/bin/bash
# ============================================================
#  Build script for Fluorescence Channel Merger (macOS .app)
# ============================================================
#  Run this on a Mac that has Python 3 installed.
#  It sets up a private Python environment (.venv-mac), installs
#  the required packages there, and uses PyInstaller to produce
#  "dist-mac/Fluorescence Channel Merger.app" plus a .zip of it
#  that's ready to share.
#
#  To use a specific Python:  PYTHON=/path/to/python3 ./build_mac.sh
# ============================================================

set -e
cd "$(dirname "$0")"

PYTHON="${PYTHON:-python3}"
APP_NAME="Fluorescence Channel Merger"

if ! "$PYTHON" -c "import tkinter" 2>/dev/null; then
    echo "ERROR: this Python ($PYTHON) doesn't include tkinter, which the app needs."
    echo "Install Python from python.org (includes tkinter), or with Homebrew run:"
    echo "    brew install python-tk"
    exit 1
fi

echo "Setting up a private Python environment in .venv-mac ..."
"$PYTHON" -m venv .venv-mac
source .venv-mac/bin/activate

echo ""
echo "Installing required packages..."
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

echo ""
echo "Building the app..."
python -m PyInstaller --noconfirm --distpath dist-mac --workpath build-mac \
    FluorescenceChannelMerger-mac.spec

ZIP_NAME="FluorescenceChannelMerger-v4.0-macOS-$(uname -m).zip"
echo ""
echo "Zipping it for sharing..."
(cd dist-mac && rm -f "$ZIP_NAME" && ditto -c -k --keepParent "$APP_NAME.app" "$ZIP_NAME")

echo ""
echo "============================================================"
echo "Build complete:"
echo "  App:         dist-mac/$APP_NAME.app"
echo "  To share:    dist-mac/$ZIP_NAME"
echo "============================================================"
