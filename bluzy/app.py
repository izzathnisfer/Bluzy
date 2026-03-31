# Bluzy — GTK Application
# Copyright (c) 2026 Bluzy Contributors
# SPDX-License-Identifier: MIT

"""
Adw.Application subclass that orchestrates all Bluzy components.

Responsibilities:
- Lifecycle management (startup, activate, shutdown)
- CSS loading
- Component wiring (BluetoothManager, AudioManager, ConfigManager)
- D-Bus service for tray ↔ app IPC
- Tray helper subprocess management
- Window hide-on-close (minimize to tray)
"""

import logging
import os
import signal
import subprocess
import sys
from pathlib import Path
from typing import Optional

import dbus
import dbus.mainloop.glib

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gdk, Gio, GLib, Gtk

from bluzy import __app_id__, __app_name__, __version__
from bluzy.audio_manager import AudioManager
from bluzy.bluetooth_manager import BluetoothManager, SpeakerState
from bluzy.config_manager import ConfigManager
from bluzy.tray_dbus import BluzyDBusService
from bluzy.utils import (
    check_adapter_exists,
    check_bluetooth_group,
    check_bluetooth_service,
    get_system_bus,
)
from bluzy.window import BluzyWindow

logger = logging.getLogger(__name__)


class BluzyApp(Adw.Application):
    """
    Main Bluzy application.

    Manages the full lifecycle of the Bluetooth Speaker Mode tool.
    """

    def __init__(self, start_minimized: bool = False) -> None:
        super().__init__(
            application_id=__app_id__,
            flags=Gio.ApplicationFlags.FLAGS_NONE,
        )
        self._start_minimized = start_minimized
        self._window: Optional[BluzyWindow] = None
        self._config: Optional[ConfigManager] = None
        self._bt_manager: Optional[BluetoothManager] = None
        self._audio_manager: Optional[AudioManager] = None
        self._dbus_service: Optional[BluzyDBusService] = None
        self._tray_process: Optional[subprocess.Popen] = None
        self._bus: Optional[dbus.SystemBus] = None

    # ==================================================================
    # Application Lifecycle
    # ==================================================================

    def do_startup(self) -> None:
        """Called once when the application first starts."""
        Adw.Application.do_startup(self)

        # Load custom CSS
        self._load_css()

        # Initialize configuration
        self._config = ConfigManager()

        # Initialize managers
        self._audio_manager = AudioManager()
        self._bt_manager = BluetoothManager(self._config)

        # Set up D-Bus IPC service for tray communication
        self._setup_dbus_service()

        # Connect BT state changes to D-Bus signals for tray
        self._bt_manager.connect(
            "state-changed", self._on_bt_state_for_tray
        )
        self._bt_manager.connect(
            "device-connected", self._on_bt_device_for_tray
        )
        self._bt_manager.connect(
            "device-disconnected", self._on_bt_disconnect_for_tray
        )

        # Set up quit action
        quit_action = Gio.SimpleAction.new("quit", None)
        quit_action.connect("activate", self._on_quit_action)
        self.add_action(quit_action)

        logger.info("Bluzy v%s started.", __version__)

    def do_activate(self) -> None:
        """Called when the application is activated (or re-activated)."""
        if not self._window:
            # Run pre-flight checks
            if not self._run_preflight_checks():
                return

            # Initialize Bluetooth
            self._bt_manager.initialize()

            # Create main window
            self._window = BluzyWindow(
                app=self,
                bt_manager=self._bt_manager,
                audio_manager=self._audio_manager,
                config=self._config,
            )

            # Override close to hide instead of quit
            self._window.connect(
                "close-request", self._on_window_close_request
            )

            # Launch tray helper
            self._launch_tray_helper()

        if not self._start_minimized:
            self._window.present()
        else:
            # Only minimize on first launch
            self._start_minimized = False

    def do_shutdown(self) -> None:
        """Called when the application is shutting down."""
        logger.info("Shutting down Bluzy…")

        # Clean up managers
        if self._bt_manager:
            self._bt_manager.cleanup()
        if self._audio_manager:
            self._audio_manager.cleanup()

        # Terminate tray helper
        self._stop_tray_helper()

        Adw.Application.do_shutdown(self)

    # ==================================================================
    # CSS Loading
    # ==================================================================

    def _load_css(self) -> None:
        """Load custom CSS styles."""
        css_paths = [
            # Development: relative to the script
            Path(__file__).parent.parent / "resources" / "style.css",
            # Installed: in the data directory
            Path.home() / ".local" / "share" / "bluzy" / "resources" / "style.css",
        ]

        for css_path in css_paths:
            if css_path.exists():
                provider = Gtk.CssProvider()
                provider.load_from_path(str(css_path))
                Gtk.StyleContext.add_provider_for_display(
                    Gdk.Display.get_default(),
                    provider,
                    Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
                )
                logger.info("CSS loaded from %s", css_path)
                return

        logger.warning("Custom CSS file not found.")

    # ==================================================================
    # Pre-flight Checks
    # ==================================================================

    def _run_preflight_checks(self) -> bool:
        """
        Run system checks before enabling Bluetooth functionality.

        Returns True if all checks pass.
        """
        # Check bluetooth group membership
        if not check_bluetooth_group():
            self._show_error_dialog(
                "Bluetooth Group Required",
                (
                    "Your user account is not in the 'bluetooth' group.\n\n"
                    "Run the following command, then log out and back in:\n\n"
                    "sudo usermod -aG bluetooth $USER"
                ),
            )
            return False

        # Check bluetooth service
        if not check_bluetooth_service():
            self._show_error_dialog(
                "Bluetooth Service Not Running",
                (
                    "The Bluetooth system service is not running.\n\n"
                    "Start it with:\n\n"
                    "sudo systemctl start bluetooth\n"
                    "sudo systemctl enable bluetooth"
                ),
            )
            return False

        # Check adapter availability
        try:
            bus = get_system_bus()
            if not check_adapter_exists(bus):
                self._show_error_dialog(
                    "No Bluetooth Adapter",
                    (
                        "No Bluetooth hardware was detected.\n\n"
                        "Please ensure your Bluetooth adapter is "
                        "enabled and not blocked by rfkill.\n\n"
                        "Run: rfkill unblock bluetooth"
                    ),
                )
                return False
        except Exception as exc:
            self._show_error_dialog(
                "Bluetooth Error",
                f"Failed to access Bluetooth: {exc}",
            )
            return False

        return True

    def _show_error_dialog(self, title: str, body: str) -> None:
        """Show a blocking error dialog."""
        # Create a temporary window for the dialog
        temp_win = Adw.ApplicationWindow(application=self)

        dialog = Adw.AlertDialog(heading=title, body=body)
        dialog.add_response("ok", "OK")
        dialog.set_response_appearance(
            "ok", Adw.ResponseAppearance.DESTRUCTIVE
        )
        dialog.connect("response", lambda _d, _r: self.quit())
        dialog.present(temp_win)

    # ==================================================================
    # D-Bus Service for Tray
    # ==================================================================

    def _setup_dbus_service(self) -> None:
        """Register the D-Bus service for tray communication."""
        try:
            session_bus = dbus.SessionBus()
            self._dbus_service = BluzyDBusService(session_bus)

            # Wire up callbacks
            self._dbus_service.on_toggle = self._dbus_toggle
            self._dbus_service.on_show = self._dbus_show
            self._dbus_service.on_quit = self._dbus_quit
            self._dbus_service.on_get_state = self._dbus_get_state

            logger.info("D-Bus IPC service ready.")
        except dbus.exceptions.DBusException as exc:
            logger.warning("Failed to set up D-Bus service: %s", exc)

    def _dbus_toggle(self) -> None:
        """Handle tray toggle request."""
        if self._window and self._window._switch_row:
            GLib.idle_add(
                self._window._switch_row.set_active,
                not self._window._switch_row.get_active(),
            )

    def _dbus_show(self) -> None:
        """Handle tray show request."""
        if self._window:
            GLib.idle_add(self._window.present)

    def _dbus_quit(self) -> None:
        """Handle tray quit request."""
        GLib.idle_add(self.quit)

    def _dbus_get_state(self) -> tuple:
        """Return current state for tray."""
        if not self._bt_manager:
            return (False, False, "")

        enabled = self._bt_manager.is_enabled
        streaming = self._bt_manager.state == SpeakerState.STREAMING
        devices = self._bt_manager.connected_devices
        device_name = ""
        if devices:
            first = next(iter(devices.values()))
            device_name = first.get("name", "")

        return (enabled, streaming, device_name)

    # ------------------------------------------------------------------
    # D-Bus tray state forwarding
    # ------------------------------------------------------------------

    def _on_bt_state_for_tray(self, _manager, state_name: str) -> None:
        """Forward state changes to the tray via D-Bus."""
        if self._dbus_service:
            enabled, streaming, name = self._dbus_get_state()
            self._dbus_service.notify_state_changed(
                enabled, streaming, name
            )

    def _on_bt_device_for_tray(
        self, _manager, name: str, address: str
    ) -> None:
        """Forward device connection to tray."""
        if self._dbus_service:
            self._dbus_service.notify_state_changed(True, False, name)

    def _on_bt_disconnect_for_tray(
        self, _manager, address: str
    ) -> None:
        """Forward device disconnection to tray."""
        if self._dbus_service:
            enabled = self._bt_manager.is_enabled if self._bt_manager else False
            self._dbus_service.notify_state_changed(enabled, False, "")

    # ==================================================================
    # Tray Helper Process
    # ==================================================================

    def _launch_tray_helper(self) -> None:
        """Launch the GTK3 tray helper as a subprocess."""
        tray_script_paths = [
            # Development: relative
            Path(__file__).parent / "tray.py",
            # Installed: inside the bluzy package dir
            Path.home() / ".local" / "share" / "bluzy" / "bluzy" / "tray.py",
        ]

        tray_script = None
        for p in tray_script_paths:
            if p.exists():
                tray_script = p
                break

        if not tray_script:
            logger.warning(
                "Tray helper script not found; running without tray."
            )
            return

        try:
            self._tray_process = subprocess.Popen(
                [sys.executable, str(tray_script)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            logger.info("Tray helper launched (PID %d).", self._tray_process.pid)
        except OSError as exc:
            logger.warning("Failed to launch tray helper: %s", exc)

    def _stop_tray_helper(self) -> None:
        """Terminate the tray helper process."""
        if self._tray_process:
            try:
                self._tray_process.terminate()
                self._tray_process.wait(timeout=3)
                logger.info("Tray helper terminated.")
            except Exception as exc:
                logger.warning("Error stopping tray helper: %s", exc)
                try:
                    self._tray_process.kill()
                except Exception:
                    pass

    # ==================================================================
    # Window Close Handling
    # ==================================================================

    def _on_window_close_request(self, window) -> bool:
        """
        Hide the window instead of quitting when the close button is pressed.

        The app continues running in the system tray.
        """
        window.set_visible(False)
        logger.debug("Window hidden (minimized to tray).")
        return True  # Prevent default close behavior

    def _on_quit_action(self, _action, _param) -> None:
        """Handle the quit action."""
        self.quit()
