#!/bin/bash
cd "$(dirname "$0")"
clear
echo ""
echo "  ================================================"
echo "   The Gathering Church - Directory Generator"
echo "  ================================================"
echo ""

# Find Python
PYTHON=""
for p in python3 /opt/homebrew/bin/python3 /usr/local/bin/python3; do
    if command -v "$p" &>/dev/null; then PYTHON="$p"; break; fi
done

if [ -z "$PYTHON" ]; then
    echo "  Python is not installed."
    echo "  Opening the download page — install it, then double-click this file again."
    open "https://www.python.org/ftp/python/3.11.9/python-3.11.9-macos11.pkg"
    read -p "  Press Enter to close..." _
    exit 1
fi

# First-time setup
if [ ! -f ".setup_done" ]; then
    echo "  First time setup - installing components..."
    echo "  This takes about 1 minute. Please wait..."
    echo ""
    $PYTHON -m pip install -q requests keyring cryptography rapidfuzz Pillow Jinja2
    touch .setup_done
    echo "  Setup complete!"
    echo ""
fi

echo "  Starting..."
$PYTHON main.py
