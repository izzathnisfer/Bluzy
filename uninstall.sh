#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════
# Bluzy — Uninstaller (for install.sh installations)
# Copyright (c) 2026 Bluzy Contributors
# SPDX-License-Identifier: MIT
#
# Usage: bash uninstall.sh
# ═══════════════════════════════════════════════════════════════════════

set -euo pipefail

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
BOLD='\033[1m'
NC='\033[0m'

APP_NAME="Bluzy"
INSTALL_DIR="$HOME/.local/share/bluzy"
ICON_BASE="$HOME/.local/share/icons/hicolor"
DESKTOP_DIR="$HOME/.local/share/applications"
DESKTOP_FILE="com.github.izzathnisfer.bluzy.desktop"
CONFIG_DIR="$HOME/.config/bluzy"

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

# ─────────────────────────────────────────────────────────────────────
# Pre-flight check
# ─────────────────────────────────────────────────────────────────────

echo ""
echo -e "${BOLD}═══════════════════════════════════════════${NC}"
echo -e "${BOLD}  🗑️  ${APP_NAME} Uninstaller${NC}"
echo -e "${BOLD}═══════════════════════════════════════════${NC}"
echo ""

if [ ! -d "$INSTALL_DIR" ] && ! ls "$DESKTOP_DIR"/*bluzy*.desktop &>/dev/null; then
    warn "Bluzy does not appear to be installed (via install.sh)."
    warn "If you installed via .deb, use: sudo apt remove bluzy"
    exit 0
fi

# ─────────────────────────────────────────────────────────────────────
# Step 1: Kill running processes
# ─────────────────────────────────────────────────────────────────────

info "Stopping any running Bluzy processes…"
pkill -f "$INSTALL_DIR/launch.py" 2>/dev/null || true
pkill -f "bluzy/tray.py" 2>/dev/null || true
sleep 0.5

# ─────────────────────────────────────────────────────────────────────
# Step 2: Remove application files
# ─────────────────────────────────────────────────────────────────────

if [ -d "$INSTALL_DIR" ]; then
    info "Removing application files…"
    rm -rf "$INSTALL_DIR"
    success "Removed $INSTALL_DIR"
else
    warn "Application directory not found — skipping."
fi

# ─────────────────────────────────────────────────────────────────────
# Step 3: Remove desktop entry
# ─────────────────────────────────────────────────────────────────────

if [ -f "$DESKTOP_DIR/$DESKTOP_FILE" ]; then
    info "Removing desktop entry…"
    rm -f "$DESKTOP_DIR/$DESKTOP_FILE"
    success "Removed desktop file."
else
    warn "Desktop file not found — skipping."
fi

# ─────────────────────────────────────────────────────────────────────
# Step 4: Remove icons
# ─────────────────────────────────────────────────────────────────────

info "Removing icons…"

REMOVED_ICONS=0

# PNG icons at various sizes
for size in 128 256 512; do
    ICON_PATH="$ICON_BASE/${size}x${size}/apps/bluzy.png"
    if [ -f "$ICON_PATH" ]; then
        rm -f "$ICON_PATH"
        ((REMOVED_ICONS++))
    fi
done

# Scalable icons
for icon in bluzy.png bluzy-tray-off.svg bluzy-tray-on.svg bluzy-tray-streaming.svg; do
    ICON_PATH="$ICON_BASE/scalable/apps/$icon"
    if [ -f "$ICON_PATH" ]; then
        rm -f "$ICON_PATH"
        ((REMOVED_ICONS++))
    fi
done

if [ "$REMOVED_ICONS" -gt 0 ]; then
    success "Removed $REMOVED_ICONS icon(s)."
else
    warn "No icons found — skipping."
fi

# ─────────────────────────────────────────────────────────────────────
# Step 5: Update caches
# ─────────────────────────────────────────────────────────────────────

info "Updating desktop database and icon cache…"

if command -v update-desktop-database &>/dev/null; then
    update-desktop-database "$DESKTOP_DIR" 2>/dev/null || true
fi

if command -v gtk-update-icon-cache &>/dev/null; then
    gtk-update-icon-cache -f -t "$ICON_BASE/" 2>/dev/null || true
fi

success "Caches updated."

# ─────────────────────────────────────────────────────────────────────
# Step 6: Handle user config
# ─────────────────────────────────────────────────────────────────────

if [ -d "$CONFIG_DIR" ]; then
    echo ""
    warn "User settings found at: $CONFIG_DIR"
    warn "These were NOT removed (your preferences are preserved)."
    warn "To delete them manually: rm -rf $CONFIG_DIR"
fi

# ─────────────────────────────────────────────────────────────────────
# Done
# ─────────────────────────────────────────────────────────────────────

echo ""
echo -e "${BOLD}═══════════════════════════════════════════${NC}"
echo -e "${GREEN}${BOLD}  ✅ ${APP_NAME} uninstalled successfully!${NC}"
echo -e "${BOLD}═══════════════════════════════════════════${NC}"
echo ""
