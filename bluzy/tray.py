#!/usr/bin/env python3
# Bluzy — System Tray Helper (GTK3)
# Copyright (c) 2026 Bluzy Contributors
# SPDX-License-Identifier: MIT

"""
Standalone GTK3 process for the system tray / AppIndicator icon.

This process runs separately from the main GTK4 application because
AppIndicator3 is built on GTK3 and cannot coexist with GTK4 in the
same process.

Communication with the main app uses a private D-Bus interface:
``com.github.bluzy.App`` on the session bus.

Icon states:
    - bluzy-tray-off        → grey Bluetooth (speaker mode off)
    - bluzy-tray-on         → blue Bluetooth (waiting for connection)
    - bluzy-tray-streaming  → green speaker  (actively streaming)
"""

import logging
import os
import signal
import sys
from pathlib import Path

# Must use GTK 3.0 for AppIndicator3
import gi

gi.require_version("Gtk", "3.0")
gi.require_version("AppIndicator3", "0.1")

from gi.repository import AppIndicator3, GLib, Gtk

import dbus
import dbus.mainloop.glib

# Set up D-Bus integration before anything else
dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)

logger = logging.getLogger("bluzy.tray")

# D-Bus constants (must match tray_dbus.py)
BLUZY_BUS_NAME = "com.github.bluzy.App"
BLUZY_OBJECT_PATH = "/com/github/bluzy"
BLUZY_IFACE = "com.github.bluzy.App"

# Icon names — installed to the icon theme or referenced by path
ICON_OFF = "bluzy-tray-off"
ICON_ON = "bluzy-tray-on"
ICON_STREAMING = "bluzy-tray-streaming"


class BluzyTray:
    """
    AppIndicator3-based system tray icon for Bluzy.

    Provides:
    - Icon that changes based on app state
    - Right-click menu with toggle, open, and quit actions
    """

    def __init__(self) -> None:
        self._indicator: AppIndicator3.Indicator = None
        self._menu: Gtk.Menu = None
        self._toggle_item: Gtk.MenuItem = None
        self._status_item: Gtk.MenuItem = None
        self._bus: dbus.SessionBus = None
        self._proxy = None
        self._iface = None
        self._is_enabled = False
        self._is_streaming = False
        self._device_name = ""

        self._setup_icon_path()
        self._create_indicator()
        self._create_menu()
        self._connect_dbus()

    # ------------------------------------------------------------------
    # Indicator setup
    # ------------------------------------------------------------------

    def _setup_icon_path(self) -> None:
        """Add custom icon directories to the search path."""
        icon_dirs = [
            # Development
            str(Path(__file__).parent.parent / "resources"),
            # Installed
            os.path.expanduser(
                "~/.local/share/icons/hicolor/scalable/apps"
            ),
            os.path.expanduser("~/.local/share/bluzy/resources"),
        ]

        for d in icon_dirs:
            if os.path.isdir(d):
                self._icon_theme_path = d
                break
        else:
            self._icon_theme_path = ""

    def _create_indicator(self) -> None:
        """Create the AppIndicator3 indicator."""
        self._indicator = AppIndicator3.Indicator.new(
            "bluzy-tray",
            ICON_OFF,
            AppIndicator3.IndicatorCategory.APPLICATION_STATUS,
        )
        self._indicator.set_status(
            AppIndicator3.IndicatorStatus.ACTIVE
        )

        # Set icon search path
        if self._icon_theme_path:
            self._indicator.set_icon_theme_path(self._icon_theme_path)

        # Fallback to a standard icon if custom ones aren't found
        self._indicator.set_attention_icon_full(
            ICON_STREAMING, "Streaming audio"
        )

        logger.info("Tray indicator created.")

    def _create_menu(self) -> None:
        """Build the right-click context menu."""
        self._menu = Gtk.Menu()

        # Status display (non-clickable)
        self._status_item = Gtk.MenuItem(label="Speaker Mode: Off")
        self._status_item.set_sensitive(False)
        self._menu.append(self._status_item)

        self._menu.append(Gtk.SeparatorMenuItem())

        # Toggle speaker mode
        self._toggle_item = Gtk.MenuItem(label="Enable Speaker Mode")
        self._toggle_item.connect("activate", self._on_toggle_clicked)
        self._menu.append(self._toggle_item)

        # Open main window
        open_item = Gtk.MenuItem(label="Open Bluzy")
        open_item.connect("activate", self._on_open_clicked)
        self._menu.append(open_item)

        self._menu.append(Gtk.SeparatorMenuItem())

        # Quit
        quit_item = Gtk.MenuItem(label="Quit")
        quit_item.connect("activate", self._on_quit_clicked)
        self._menu.append(quit_item)

        self._menu.show_all()
        self._indicator.set_menu(self._menu)

    # ------------------------------------------------------------------
    # D-Bus connection
    # ------------------------------------------------------------------

    def _connect_dbus(self) -> None:
        """Connect to the main Bluzy app via D-Bus."""
        self._bus = dbus.SessionBus()

        # Retry connection to the main app (it may start after us)
        GLib.timeout_add_seconds(1, self._try_connect)

    def _try_connect(self) -> bool:
        """Attempt to connect to the main app's D-Bus service."""
        try:
            self._proxy = self._bus.get_object(
                BLUZY_BUS_NAME, BLUZY_OBJECT_PATH
            )
            self._iface = dbus.Interface(self._proxy, BLUZY_IFACE)

            # Listen for state changes
            self._bus.add_signal_receiver(
                self._on_state_changed,
                dbus_interface=BLUZY_IFACE,
                signal_name="StateChanged",
            )

            # Get initial state
            try:
                state = self._iface.GetState()
                self._update_state(
                    bool(state[0]), bool(state[1]), str(state[2])
                )
            except dbus.exceptions.DBusException:
                pass

            logger.info("Connected to Bluzy main app via D-Bus.")
            return False  # Stop retrying

        except dbus.exceptions.DBusException:
            logger.debug("Main app not ready yet, retrying…")
            return True  # Keep retrying

    # ------------------------------------------------------------------
    # State updates
    # ------------------------------------------------------------------

    def _on_state_changed(
        self, enabled: bool, streaming: bool, device_name: str
    ) -> None:
        """Handle StateChanged signal from the main app."""
        self._update_state(bool(enabled), bool(streaming), str(device_name))

    def _update_state(
        self, enabled: bool, streaming: bool, device_name: str
    ) -> None:
        """Update icon and menu based on current state."""
        self._is_enabled = enabled
        self._is_streaming = streaming
        self._device_name = device_name

        # Update icon
        if streaming:
            self._indicator.set_icon_full(ICON_STREAMING, "Streaming")
        elif enabled:
            self._indicator.set_icon_full(ICON_ON, "Waiting")
        else:
            self._indicator.set_icon_full(ICON_OFF, "Off")

        # Update menu items
        if streaming and device_name:
            self._status_item.set_label(f"Streaming: {device_name}")
        elif enabled and device_name:
            self._status_item.set_label(f"Connected: {device_name}")
        elif enabled:
            self._status_item.set_label("Speaker Mode: Waiting…")
        else:
            self._status_item.set_label("Speaker Mode: Off")

        self._toggle_item.set_label(
            "Disable Speaker Mode" if enabled else "Enable Speaker Mode"
        )

    # ------------------------------------------------------------------
    # Menu actions
    # ------------------------------------------------------------------

    def _on_toggle_clicked(self, _widget) -> None:
        """Toggle speaker mode via D-Bus."""
        if self._iface:
            try:
                self._iface.Toggle()
            except dbus.exceptions.DBusException as exc:
                logger.error("Failed to toggle: %s", exc)

    def _on_open_clicked(self, _widget) -> None:
        """Show the main window via D-Bus."""
        if self._iface:
            try:
                self._iface.Show()
            except dbus.exceptions.DBusException as exc:
                logger.error("Failed to show window: %s", exc)

    def _on_quit_clicked(self, _widget) -> None:
        """Quit both the tray and the main app."""
        if self._iface:
            try:
                self._iface.Quit()
            except dbus.exceptions.DBusException:
                pass
        Gtk.main_quit()


def main() -> None:
    """Tray helper entry point."""
    # Logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)-7s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    # Allow Ctrl+C
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    signal.signal(signal.SIGTERM, lambda *_: Gtk.main_quit())

    logger.info("Bluzy tray helper starting…")
    tray = BluzyTray()
    Gtk.main()
    logger.info("Bluzy tray helper exiting.")


if __name__ == "__main__":
    main()
