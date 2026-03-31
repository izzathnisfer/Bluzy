#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════
# Bluzy — One-Command Installer
# Copyright (c) 2026 Bluzy Contributors
# SPDX-License-Identifier: MIT
#
# Usage: bash install.sh
# ═══════════════════════════════════════════════════════════════════════

set -euo pipefail

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
BOLD='\033[1m'
NC='\033[0m'  # No Color

APP_NAME="Bluzy"
INSTALL_DIR="$HOME/.local/share/bluzy"
ICON_DIR="$HOME/.local/share/icons/hicolor/scalable/apps"
DESKTOP_DIR="$HOME/.local/share/applications"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ─────────────────────────────────────────────────────────────────────
# Helper functions
# ─────────────────────────────────────────────────────────────────────

info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

success() {
    echo -e "${GREEN}[✓]${NC} $1"
}

warn() {
    echo -e "${YELLOW}[!]${NC} $1"
}

error() {
    echo -e "${RED}[✗]${NC} $1"
    exit 1
}

# ─────────────────────────────────────────────────────────────────────
# Step 1: Check prerequisites
# ─────────────────────────────────────────────────────────────────────

echo ""
echo -e "${BOLD}═══════════════════════════════════════════${NC}"
echo -e "${BOLD}  🔊 ${APP_NAME} Installer${NC}"
echo -e "${BOLD}═══════════════════════════════════════════${NC}"
echo ""

# Check we're on a supported system
if ! command -v apt &>/dev/null; then
    error "This installer requires apt (Ubuntu/Debian). Please install dependencies manually."
fi

if ! command -v python3 &>/dev/null; then
    error "Python 3 is required but not found."
fi

info "Checking Python version…"
PYTHON_VERSION=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
info "Python $PYTHON_VERSION detected."

# ─────────────────────────────────────────────────────────────────────
# Step 2: Install system dependencies
# ─────────────────────────────────────────────────────────────────────

info "Installing system dependencies (requires sudo)…"
echo ""

sudo apt update -qq

sudo apt install -y \
    python3-gi \
    python3-gi-cairo \
    gir1.2-gtk-4.0 \
    gir1.2-adw-1 \
    gir1.2-appindicator3-0.1 \
    python3-dbus \
    bluez \
    pipewire \
    wireplumber \
    pulseaudio-utils

echo ""
success "System dependencies installed."

# ─────────────────────────────────────────────────────────────────────
# Step 3: Ensure Bluetooth service is running
# ─────────────────────────────────────────────────────────────────────

info "Checking Bluetooth service…"

if systemctl is-active --quiet bluetooth; then
    success "Bluetooth service is running."
else
    warn "Bluetooth service is not running. Starting it…"
    sudo systemctl start bluetooth
    sudo systemctl enable bluetooth
    success "Bluetooth service started and enabled."
fi

# ─────────────────────────────────────────────────────────────────────
# Step 4: Check bluetooth group membership
# ─────────────────────────────────────────────────────────────────────

info "Checking bluetooth group membership…"

if groups "$USER" | grep -qw bluetooth 2>/dev/null; then
    success "User '$USER' is in the bluetooth group."
else
    warn "User '$USER' is NOT in the bluetooth group."
    info "Adding user to bluetooth group…"
    sudo usermod -aG bluetooth "$USER"
    warn "You will need to LOG OUT and LOG BACK IN for this to take effect."
fi

# ─────────────────────────────────────────────────────────────────────
# Step 5: Install application files
# ─────────────────────────────────────────────────────────────────────

info "Installing application files to $INSTALL_DIR…"

# Clean previous installation
rm -rf "$INSTALL_DIR"
mkdir -p "$INSTALL_DIR"
mkdir -p "$INSTALL_DIR/bluzy"
mkdir -p "$INSTALL_DIR/resources"

# Copy Python package (preserves 'from bluzy import ...' imports)
cp "$SCRIPT_DIR/bluzy/"*.py "$INSTALL_DIR/bluzy/"

# Create a top-level launch script
cat > "$INSTALL_DIR/launch.py" << 'LAUNCH_EOF'
#!/usr/bin/env python3
"""Bluzy launcher — adds the install dir to sys.path and runs main."""
import os
import sys

# Ensure the install directory is on the path for 'from bluzy import ...'
install_dir = os.path.dirname(os.path.abspath(__file__))
if install_dir not in sys.path:
    sys.path.insert(0, install_dir)

from bluzy.main import main
sys.exit(main())
LAUNCH_EOF
chmod +x "$INSTALL_DIR/launch.py"

# Copy resources
cp "$SCRIPT_DIR/resources/"*.css "$INSTALL_DIR/resources/"
cp "$SCRIPT_DIR/resources/"*.svg "$INSTALL_DIR/resources/" 2>/dev/null || true
cp "$SCRIPT_DIR/resources/"*.png "$INSTALL_DIR/resources/" 2>/dev/null || true
cp "$SCRIPT_DIR/resources/"*.xml "$INSTALL_DIR/resources/" 2>/dev/null || true

success "Application files installed."

# ─────────────────────────────────────────────────────────────────────
# Step 6: Install icon
# ─────────────────────────────────────────────────────────────────────

info "Installing application icon…"

# Install main app icon as PNG at multiple sizes for dock, app grid, etc.
PNG_ICON="$SCRIPT_DIR/resources/bluzy.png"
if [ -f "$PNG_ICON" ]; then
    for size in 128 256 512; do
        SIZE_DIR="$HOME/.local/share/icons/hicolor/${size}x${size}/apps"
        mkdir -p "$SIZE_DIR"
        cp "$PNG_ICON" "$SIZE_DIR/bluzy.png"
    done
    # Also install to the scalable directory as fallback
    mkdir -p "$ICON_DIR"
    cp "$PNG_ICON" "$ICON_DIR/bluzy.png"
fi

# Install SVG tray icons
mkdir -p "$ICON_DIR"
cp "$SCRIPT_DIR/resources/bluzy-tray-off.svg" "$ICON_DIR/" 2>/dev/null || true
cp "$SCRIPT_DIR/resources/bluzy-tray-on.svg" "$ICON_DIR/" 2>/dev/null || true
cp "$SCRIPT_DIR/resources/bluzy-tray-streaming.svg" "$ICON_DIR/" 2>/dev/null || true

success "Icons installed."

# ─────────────────────────────────────────────────────────────────────
# Step 7: Install .desktop file
# ─────────────────────────────────────────────────────────────────────

info "Installing desktop entry…"

mkdir -p "$DESKTOP_DIR"
cp "$SCRIPT_DIR/data/com.github.izzathnisfer.bluzy.desktop" "$DESKTOP_DIR/"

# Update the Exec path and Icon to use the installed location
sed -i "s|Exec=.*|Exec=python3 $INSTALL_DIR/launch.py|" \
    "$DESKTOP_DIR/com.github.izzathnisfer.bluzy.desktop"
sed -i "s|Icon=.*|Icon=bluzy|" \
    "$DESKTOP_DIR/com.github.izzathnisfer.bluzy.desktop"

success "Desktop entry installed."

# ─────────────────────────────────────────────────────────────────────
# Step 8: Update desktop database and icon cache
# ─────────────────────────────────────────────────────────────────────

info "Updating desktop database…"

if command -v update-desktop-database &>/dev/null; then
    update-desktop-database "$DESKTOP_DIR" 2>/dev/null || true
fi

if command -v gtk-update-icon-cache &>/dev/null; then
    gtk-update-icon-cache "$HOME/.local/share/icons/hicolor/" 2>/dev/null || true
fi

success "Databases updated."

# ─────────────────────────────────────────────────────────────────────
# Done!
# ─────────────────────────────────────────────────────────────────────

echo ""
echo -e "${BOLD}═══════════════════════════════════════════${NC}"
echo -e "${GREEN}${BOLD}  ✅ ${APP_NAME} installed successfully!${NC}"
echo -e "${BOLD}═══════════════════════════════════════════${NC}"
echo ""
echo -e "  Launch from your app drawer or run:"
echo -e "  ${BLUE}python3 $INSTALL_DIR/launch.py${NC}"
echo ""

if ! groups "$USER" | grep -qw bluetooth 2>/dev/null; then
    echo -e "${YELLOW}  ⚠  Remember to log out and back in for${NC}"
    echo -e "${YELLOW}     bluetooth group changes to take effect!${NC}"
    echo ""
fi
