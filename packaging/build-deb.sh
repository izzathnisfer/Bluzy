#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════
# Bluzy — .deb Package Builder
# Copyright (c) 2026 Bluzy Contributors
# SPDX-License-Identifier: MIT
#
# Usage: bash packaging/build-deb.sh
# Output: dist/bluzy_<version>_all.deb
# ═══════════════════════════════════════════════════════════════════════

set -euo pipefail

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
BOLD='\033[1m'
NC='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

# Extract version from Python source
VERSION=$(python3 -c "
import re
with open('$PROJECT_DIR/bluzy/__init__.py') as f:
    m = re.search(r'__version__\s*=\s*\"(.+?)\"', f.read())
    print(m.group(1))
")

PKG_NAME="bluzy"
ARCH="all"
DEB_NAME="${PKG_NAME}_${VERSION}_${ARCH}"
DIST_DIR="$PROJECT_DIR/dist"
STAGING="$DIST_DIR/$DEB_NAME"

echo ""
echo -e "${BOLD}═══════════════════════════════════════════${NC}"
echo -e "${BOLD}  📦 Bluzy .deb Builder${NC}"
echo -e "${BOLD}═══════════════════════════════════════════${NC}"
echo -e "  Version : ${BLUE}${VERSION}${NC}"
echo -e "  Package : ${BLUE}${DEB_NAME}.deb${NC}"
echo ""

# ------------------------------------------------------------------
# Step 1: Clean previous build
# ------------------------------------------------------------------

rm -rf "$STAGING"
mkdir -p "$DIST_DIR"

# ------------------------------------------------------------------
# Step 2: Create directory structure
# ------------------------------------------------------------------

echo -e "${BLUE}[1/5]${NC} Creating package structure…"

# Application files → /opt/bluzy/
mkdir -p "$STAGING/opt/bluzy/bluzy"
mkdir -p "$STAGING/opt/bluzy/resources"

# CLI launcher → /usr/bin/
mkdir -p "$STAGING/usr/bin"

# Desktop file → /usr/share/applications/
mkdir -p "$STAGING/usr/share/applications"

# Icons → /usr/share/icons/hicolor/
for size in 128 256 512; do
    mkdir -p "$STAGING/usr/share/icons/hicolor/${size}x${size}/apps"
done
mkdir -p "$STAGING/usr/share/icons/hicolor/scalable/apps"

# AppStream metadata → /usr/share/metainfo/
mkdir -p "$STAGING/usr/share/metainfo"

# Debian control directory
mkdir -p "$STAGING/DEBIAN"

# ------------------------------------------------------------------
# Step 3: Copy application files
# ------------------------------------------------------------------

echo -e "${BLUE}[2/5]${NC} Copying application files…"

# Python package
cp "$PROJECT_DIR/bluzy/"*.py "$STAGING/opt/bluzy/bluzy/"

# Resources
cp "$PROJECT_DIR/resources/"*.css "$STAGING/opt/bluzy/resources/"
cp "$PROJECT_DIR/resources/"*.png "$STAGING/opt/bluzy/resources/" 2>/dev/null || true
cp "$PROJECT_DIR/resources/"*.svg "$STAGING/opt/bluzy/resources/" 2>/dev/null || true

# Create launch.py (entry point)
cat > "$STAGING/opt/bluzy/launch.py" << 'LAUNCH_EOF'
#!/usr/bin/env python3
"""Bluzy launcher — adds the install dir to sys.path and runs main."""
import os
import sys

install_dir = os.path.dirname(os.path.abspath(__file__))
if install_dir not in sys.path:
    sys.path.insert(0, install_dir)

from bluzy.main import main
sys.exit(main())
LAUNCH_EOF

# CLI launcher wrapper → /usr/bin/bluzy
cat > "$STAGING/usr/bin/bluzy" << 'BIN_EOF'
#!/bin/bash
exec python3 /opt/bluzy/launch.py "$@"
BIN_EOF
chmod 755 "$STAGING/usr/bin/bluzy"

# Desktop file
cat > "$STAGING/usr/share/applications/com.github.izzathnisfer.bluzy.desktop" << 'DESKTOP_EOF'
[Desktop Entry]
Name=Bluzy
GenericName=Bluetooth Speaker Mode
Comment=Turn your laptop into a Bluetooth speaker
Exec=/usr/bin/bluzy
Icon=bluzy
Type=Application
Categories=AudioVideo;Utility;GTK;
Keywords=bluetooth;speaker;audio;receiver;a2dp;wireless;
StartupNotify=true
Terminal=false
X-GNOME-UsesNotifications=true
DESKTOP_EOF

# Icons
if [ -f "$PROJECT_DIR/resources/bluzy.png" ]; then
    for size in 128 256 512; do
        cp "$PROJECT_DIR/resources/bluzy.png" \
            "$STAGING/usr/share/icons/hicolor/${size}x${size}/apps/bluzy.png"
    done
    cp "$PROJECT_DIR/resources/bluzy.png" \
        "$STAGING/usr/share/icons/hicolor/scalable/apps/bluzy.png"
fi

# Tray icons
for tray_icon in bluzy-tray-off.svg bluzy-tray-on.svg bluzy-tray-streaming.svg; do
    if [ -f "$PROJECT_DIR/resources/$tray_icon" ]; then
        cp "$PROJECT_DIR/resources/$tray_icon" \
            "$STAGING/usr/share/icons/hicolor/scalable/apps/"
    fi
done

# AppStream metainfo
if [ -f "$PROJECT_DIR/data/com.github.izzathnisfer.bluzy.metainfo.xml" ]; then
    cp "$PROJECT_DIR/data/com.github.izzathnisfer.bluzy.metainfo.xml" \
        "$STAGING/usr/share/metainfo/"
fi

# ------------------------------------------------------------------
# Step 4: Create DEBIAN control files
# ------------------------------------------------------------------

echo -e "${BLUE}[3/5]${NC} Generating Debian control files…"

# Calculate installed size (in KB)
INSTALLED_SIZE=$(du -sk "$STAGING" --exclude=DEBIAN | awk '{print $1}')

# control
cat > "$STAGING/DEBIAN/control" << CONTROL_EOF
Package: ${PKG_NAME}
Version: ${VERSION}
Section: sound
Priority: optional
Architecture: ${ARCH}
Installed-Size: ${INSTALLED_SIZE}
Depends: python3 (>= 3.10), python3-gi, python3-gi-cairo, gir1.2-gtk-4.0, gir1.2-adw-1, gir1.2-appindicator3-0.1, python3-dbus, bluez, pipewire | pulseaudio, wireplumber, pulseaudio-utils
Maintainer: Izzath Nisfer <izzathnisfer@users.noreply.github.com>
Homepage: https://github.com/izzathnisfer/Bluzy
Description: Bluetooth Speaker Mode for Ubuntu
 Bluzy turns your Ubuntu laptop into a wireless Bluetooth audio receiver
 (A2DP Sink). Connect your phone via Bluetooth and play audio through
 your laptop's speakers.
 .
 Features:
  - One-toggle Bluetooth Speaker Mode
  - Real-time connection status with animated indicators
  - System tray icon with quick controls
  - Configurable speaker name and pairing confirmation
  - Auto-connect memory for trusted devices
  - Volume control for incoming audio
  - Independent audio output selection and Bluetooth playback volume
  - Native GNOME look and feel with GTK4 + Libadwaita
CONTROL_EOF

# postinst — runs after installation
cat > "$STAGING/DEBIAN/postinst" << 'POSTINST_EOF'
#!/bin/bash
set -e

# Update icon cache
if command -v gtk-update-icon-cache &>/dev/null; then
    gtk-update-icon-cache -f -t /usr/share/icons/hicolor/ 2>/dev/null || true
fi

# Update desktop database
if command -v update-desktop-database &>/dev/null; then
    update-desktop-database /usr/share/applications/ 2>/dev/null || true
fi

echo ""
echo "  ✅ Bluzy installed successfully!"
echo "  Launch from your app grid or run: bluzy"
echo ""
POSTINST_EOF
chmod 755 "$STAGING/DEBIAN/postinst"

# prerm — runs before removal
cat > "$STAGING/DEBIAN/prerm" << 'PRERM_EOF'
#!/bin/bash
set -e

# Kill any running bluzy processes
pkill -f "/opt/bluzy/launch.py" 2>/dev/null || true
pkill -f "bluzy/tray.py" 2>/dev/null || true

# Clean up user config (optional — commented out to preserve settings)
# rm -rf "$HOME/.config/bluzy"
PRERM_EOF
chmod 755 "$STAGING/DEBIAN/prerm"

# postrm — runs after removal
cat > "$STAGING/DEBIAN/postrm" << 'POSTRM_EOF'
#!/bin/bash
set -e

# Update icon cache
if command -v gtk-update-icon-cache &>/dev/null; then
    gtk-update-icon-cache -f -t /usr/share/icons/hicolor/ 2>/dev/null || true
fi

# Update desktop database
if command -v update-desktop-database &>/dev/null; then
    update-desktop-database /usr/share/applications/ 2>/dev/null || true
fi
POSTRM_EOF
chmod 755 "$STAGING/DEBIAN/postrm"

# ------------------------------------------------------------------
# Step 5: Set correct file permissions
# ------------------------------------------------------------------

echo -e "${BLUE}[4/5]${NC} Setting permissions…"

# All files owned by root
find "$STAGING" -type d -exec chmod 755 {} +
find "$STAGING" -type f -exec chmod 644 {} +

# Executables
chmod 755 "$STAGING/usr/bin/bluzy"
chmod 755 "$STAGING/opt/bluzy/launch.py"
chmod 755 "$STAGING/DEBIAN/postinst"
chmod 755 "$STAGING/DEBIAN/prerm"
chmod 755 "$STAGING/DEBIAN/postrm"

# ------------------------------------------------------------------
# Step 6: Build the .deb
# ------------------------------------------------------------------

echo -e "${BLUE}[5/5]${NC} Building .deb package…"

dpkg-deb --build --root-owner-group "$STAGING" "$DIST_DIR/${DEB_NAME}.deb"

# Clean up staging directory
rm -rf "$STAGING"

echo ""
echo -e "${BOLD}═══════════════════════════════════════════${NC}"
echo -e "${GREEN}${BOLD}  ✅ Package built successfully!${NC}"
echo -e "${BOLD}═══════════════════════════════════════════${NC}"
echo ""
echo -e "  Output: ${BLUE}dist/${DEB_NAME}.deb${NC}"
echo ""
echo -e "  Install with:"
echo -e "    ${BLUE}sudo apt install ./dist/${DEB_NAME}.deb${NC}"
echo ""
echo -e "  Or share the .deb file — users install with:"
echo -e "    ${BLUE}sudo apt install ./bluzy_${VERSION}_all.deb${NC}"
echo ""
