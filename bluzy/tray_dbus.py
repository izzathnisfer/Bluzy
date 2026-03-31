# Bluzy — D-Bus IPC Interface
# Copyright (c) 2026 Bluzy Contributors
# SPDX-License-Identifier: MIT

"""
Private D-Bus interface for communication between the main GTK4 app
and the GTK3 system tray helper process.

The main app exports ``com.github.bluzy.App`` on the session bus.
The tray helper calls methods on this interface and listens for signals.

Interface: ``com.github.bluzy.App``
Object path: ``/com/github/bluzy``

Methods:
    Toggle()         — Toggle speaker mode on/off
    Show()           — Raise the main application window
    Quit()           — Quit both the main app and the tray helper
    GetState()       — Returns (enabled: bool, streaming: bool, device_name: str)

Signals:
    StateChanged(enabled: bool, streaming: bool, device_name: str)
"""

import logging

import dbus
import dbus.service

logger = logging.getLogger(__name__)

BLUZY_BUS_NAME = "com.github.bluzy.App"
BLUZY_OBJECT_PATH = "/com/github/bluzy"
BLUZY_IFACE = "com.github.bluzy.App"


class BluzyDBusService(dbus.service.Object):
    """
    D-Bus service exported by the main Bluzy application.

    The tray helper process connects to this service to control
    the app and receive state updates.
    """

    def __init__(self, bus: dbus.SessionBus) -> None:
        bus_name = dbus.service.BusName(BLUZY_BUS_NAME, bus=bus)
        super().__init__(bus_name, BLUZY_OBJECT_PATH)
        self._bus = bus

        # Callbacks set by app.py
        self.on_toggle = None
        self.on_show = None
        self.on_quit = None
        self.on_get_state = None

        logger.info("Bluzy D-Bus service registered at %s", BLUZY_BUS_NAME)

    # ------------------------------------------------------------------
    # Exported methods
    # ------------------------------------------------------------------

    @dbus.service.method(BLUZY_IFACE, in_signature="", out_signature="")
    def Toggle(self) -> None:
        """Toggle speaker mode."""
        logger.debug("D-Bus: Toggle() called")
        if self.on_toggle:
            self.on_toggle()

    @dbus.service.method(BLUZY_IFACE, in_signature="", out_signature="")
    def Show(self) -> None:
        """Raise the main window."""
        logger.debug("D-Bus: Show() called")
        if self.on_show:
            self.on_show()

    @dbus.service.method(BLUZY_IFACE, in_signature="", out_signature="")
    def Quit(self) -> None:
        """Quit the application."""
        logger.debug("D-Bus: Quit() called")
        if self.on_quit:
            self.on_quit()

    @dbus.service.method(
        BLUZY_IFACE, in_signature="", out_signature="(bbs)"
    )
    def GetState(self) -> tuple:
        """
        Get the current state.

        Returns:
            Tuple of (enabled, streaming, device_name).
        """
        logger.debug("D-Bus: GetState() called")
        if self.on_get_state:
            return self.on_get_state()
        return (False, False, "")

    # ------------------------------------------------------------------
    # Signals
    # ------------------------------------------------------------------

    @dbus.service.signal(BLUZY_IFACE, signature="bbs")
    def StateChanged(
        self, enabled: bool, streaming: bool, device_name: str
    ) -> None:
        """Signal emitted when the app state changes."""
        logger.debug(
            "D-Bus signal: StateChanged(%s, %s, %s)",
            enabled,
            streaming,
            device_name,
        )

    # ------------------------------------------------------------------
    # Helper to emit state changes
    # ------------------------------------------------------------------

    def notify_state_changed(
        self, enabled: bool, streaming: bool, device_name: str
    ) -> None:
        """Convenience method to emit the StateChanged signal."""
        self.StateChanged(enabled, streaming, device_name)


class BluzyDBusClient:
    """
    D-Bus client used by the tray helper to call the main app.
    """

    def __init__(self) -> None:
        self._bus = dbus.SessionBus()
        self._proxy = None
        self._iface = None
        self._state_callback = None

    def connect(self) -> bool:
        """Connect to the main app's D-Bus service."""
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

            logger.info("Connected to Bluzy D-Bus service.")
            return True

        except dbus.exceptions.DBusException as exc:
            logger.error("Failed to connect to Bluzy D-Bus: %s", exc)
            return False

    def toggle(self) -> None:
        """Toggle speaker mode."""
        if self._iface:
            self._iface.Toggle()

    def show(self) -> None:
        """Show the main window."""
        if self._iface:
            self._iface.Show()

    def quit(self) -> None:
        """Quit the application."""
        if self._iface:
            self._iface.Quit()

    def get_state(self) -> tuple:
        """Get the current state."""
        if self._iface:
            return self._iface.GetState()
        return (False, False, "")

    def set_state_callback(self, callback) -> None:
        """Set a callback for state changes: callback(enabled, streaming, name)."""
        self._state_callback = callback

    def _on_state_changed(
        self, enabled: bool, streaming: bool, device_name: str
    ) -> None:
        """Handle the StateChanged signal."""
        if self._state_callback:
            self._state_callback(
                bool(enabled), bool(streaming), str(device_name)
            )
