# Bluzy — Bluetooth Manager
# Copyright (c) 2026 Bluzy Contributors
# SPDX-License-Identifier: MIT

"""
Central Bluetooth management via BlueZ D-Bus.

Handles adapter control (discoverable, pairable, alias), device tracking,
and real-time event monitoring through D-Bus signals. Emits GObject signals
so the GTK UI can react to state changes on the main thread.
"""

import logging
from enum import Enum, auto
from typing import Optional

import dbus
import dbus.mainloop.glib

from gi.repository import GLib, GObject

from bluzy.bluetooth_agent import BluzyAgent
from bluzy.config_manager import ConfigManager
from bluzy.utils import (
    BLUEZ_ADAPTER_IFACE,
    BLUEZ_DEVICE_IFACE,
    BLUEZ_MEDIA_TRANSPORT_IFACE,
    BLUEZ_SERVICE,
    DBUS_OM_IFACE,
    DBUS_PROPS_IFACE,
    get_adapter_path,
    get_managed_objects,
    get_system_bus,
)

logger = logging.getLogger(__name__)


class SpeakerState(Enum):
    """Possible states of the speaker mode."""

    OFF = auto()
    WAITING = auto()       # Discoverable, waiting for connection
    CONNECTED = auto()     # Device connected, not yet streaming
    STREAMING = auto()     # Audio is actively streaming
    ERROR = auto()


class BluetoothManager(GObject.Object):
    """
    Manages the Bluetooth adapter for A2DP Sink (speaker) mode.

    Emits the following GObject signals:

    - ``state-changed(state: str)`` — one of SpeakerState values
    - ``device-connected(name: str, address: str)``
    - ``device-disconnected(address: str)``
    - ``streaming-started()``
    - ``streaming-stopped()``
    - ``error(message: str)``

    All signals are emitted on the GLib main thread.
    """

    __gsignals__ = {
        "state-changed": (
            GObject.SignalFlags.RUN_FIRST,
            None,
            (str,),
        ),
        "device-connected": (
            GObject.SignalFlags.RUN_FIRST,
            None,
            (str, str),  # name, address
        ),
        "device-disconnected": (
            GObject.SignalFlags.RUN_FIRST,
            None,
            (str,),  # address
        ),
        "streaming-started": (
            GObject.SignalFlags.RUN_FIRST,
            None,
            (),
        ),
        "streaming-stopped": (
            GObject.SignalFlags.RUN_FIRST,
            None,
            (),
        ),
        "error": (
            GObject.SignalFlags.RUN_FIRST,
            None,
            (str,),
        ),
        "pairing-requested": (
            GObject.SignalFlags.RUN_FIRST,
            None,
            (str, str, int),  # device_path, device_name, passkey
        ),
    }

    def __init__(self, config: ConfigManager) -> None:
        super().__init__()
        self._config = config
        self._bus: Optional[dbus.SystemBus] = None
        self._adapter_path: Optional[str] = None
        self._agent: Optional[BluzyAgent] = None
        self._state = SpeakerState.OFF
        self._connected_devices: dict[str, dict] = {}
        self._is_streaming = False
        self._signal_matches: list = []

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def state(self) -> SpeakerState:
        return self._state

    @property
    def connected_devices(self) -> dict[str, dict]:
        """Map of address → {name, path, connected}."""
        return dict(self._connected_devices)

    @property
    def is_enabled(self) -> bool:
        return self._state != SpeakerState.OFF

    # ------------------------------------------------------------------
    # Initialization
    # ------------------------------------------------------------------

    def initialize(self) -> bool:
        """
        Connect to D-Bus and locate the Bluetooth adapter.

        Returns:
            True if initialization succeeded.
        """
        try:
            self._bus = get_system_bus()
            self._adapter_path = get_adapter_path(self._bus)

            if not self._adapter_path:
                self._emit_error(
                    "No Bluetooth adapter found. "
                    "Please ensure Bluetooth hardware is available."
                )
                return False

            self._setup_signal_handlers()
            logger.info(
                "BluetoothManager initialized with adapter %s",
                self._adapter_path,
            )
            return True

        except dbus.exceptions.DBusException as exc:
            self._emit_error(f"Failed to connect to BlueZ: {exc}")
            return False

    # ------------------------------------------------------------------
    # Enable / Disable speaker mode
    # ------------------------------------------------------------------

    def enable(self) -> bool:
        """
        Enable Bluetooth Speaker Mode.

        Makes the adapter discoverable and pairable, sets a friendly alias,
        and registers the pairing agent.

        Returns:
            True if speaker mode was enabled successfully.
        """
        if not self._bus or not self._adapter_path:
            self._emit_error("Bluetooth not initialized.")
            return False

        try:
            adapter_obj = self._bus.get_object(
                BLUEZ_SERVICE, self._adapter_path
            )
            props = dbus.Interface(adapter_obj, DBUS_PROPS_IFACE)

            # Power on the adapter
            props.Set(BLUEZ_ADAPTER_IFACE, "Powered", dbus.Boolean(True))

            # Set the friendly name
            alias = self._config.speaker_alias
            props.Set(BLUEZ_ADAPTER_IFACE, "Alias", dbus.String(alias))

            # Make discoverable (infinite timeout)
            props.Set(
                BLUEZ_ADAPTER_IFACE,
                "DiscoverableTimeout",
                dbus.UInt32(0),
            )
            props.Set(
                BLUEZ_ADAPTER_IFACE, "Discoverable", dbus.Boolean(True)
            )

            # Make pairable
            props.Set(
                BLUEZ_ADAPTER_IFACE,
                "PairableTimeout",
                dbus.UInt32(0),
            )
            props.Set(
                BLUEZ_ADAPTER_IFACE, "Pairable", dbus.Boolean(True)
            )

            # Register the pairing agent
            self._register_agent()

            self._set_state(SpeakerState.WAITING)
            logger.info("Speaker mode enabled. Waiting for connections…")
            return True

        except dbus.exceptions.DBusException as exc:
            self._emit_error(f"Failed to enable speaker mode: {exc}")
            return False

    def disable(self) -> bool:
        """
        Disable Bluetooth Speaker Mode.

        Disconnects all connected audio devices, makes the adapter
        non-discoverable, and unregisters the pairing agent.

        Returns:
            True if speaker mode was disabled successfully.
        """
        if not self._bus or not self._adapter_path:
            return True  # Already effectively off

        try:
            # Disconnect all connected devices gracefully
            self._disconnect_all_devices()

            adapter_obj = self._bus.get_object(
                BLUEZ_SERVICE, self._adapter_path
            )
            props = dbus.Interface(adapter_obj, DBUS_PROPS_IFACE)

            # Make non-discoverable
            props.Set(
                BLUEZ_ADAPTER_IFACE, "Discoverable", dbus.Boolean(False)
            )

            # Make non-pairable
            props.Set(
                BLUEZ_ADAPTER_IFACE, "Pairable", dbus.Boolean(False)
            )

            # Unregister agent
            self._unregister_agent()

            self._set_state(SpeakerState.OFF)
            logger.info("Speaker mode disabled.")
            return True

        except dbus.exceptions.DBusException as exc:
            self._emit_error(f"Failed to disable speaker mode: {exc}")
            return False

    # ------------------------------------------------------------------
    # Pairing confirmation (non-blocking)
    # ------------------------------------------------------------------

    def respond_to_pairing(self, accepted: bool) -> None:
        """
        Respond to a pending pairing request from the UI.

        This sends the deferred D-Bus reply to BlueZ via the agent's
        stored async callbacks — never blocks the main loop.
        """
        if self._agent:
            self._agent.complete_confirmation(accepted)

    def _on_confirmation_needed(
        self, device_path: str, device_name: str, passkey: int
    ) -> None:
        """
        Called by the BluzyAgent (on the main thread) when pairing
        confirmation is needed from the user.

        Emits the ``pairing-requested`` GObject signal so the UI can
        show a dialog. Completely non-blocking.
        """
        logger.info(
            "Pairing confirmation needed: %s (%s), passkey=%06d",
            device_name,
            device_path,
            passkey,
        )
        self.emit("pairing-requested", device_path, device_name, passkey)

    # ------------------------------------------------------------------
    # Device management
    # ------------------------------------------------------------------

    def disconnect_device(self, address: str) -> bool:
        """Disconnect a specific device by MAC address."""
        device_info = self._connected_devices.get(address)
        if not device_info:
            return False

        try:
            device_obj = self._bus.get_object(
                BLUEZ_SERVICE, device_info["path"]
            )
            device_iface = dbus.Interface(device_obj, BLUEZ_DEVICE_IFACE)
            device_iface.Disconnect()
            logger.info("Disconnected device: %s", address)
            return True
        except dbus.exceptions.DBusException as exc:
            logger.error("Failed to disconnect %s: %s", address, exc)
            return False

    def _disconnect_all_devices(self) -> None:
        """Disconnect all currently connected Bluetooth audio devices."""
        for address in list(self._connected_devices.keys()):
            self.disconnect_device(address)

    # ------------------------------------------------------------------
    # D-Bus signal handlers
    # ------------------------------------------------------------------

    def _setup_signal_handlers(self) -> None:
        """Subscribe to BlueZ D-Bus signals for real-time updates."""
        if not self._bus:
            return

        # InterfacesAdded — new device appears
        match1 = self._bus.add_signal_receiver(
            self._on_interfaces_added,
            dbus_interface=DBUS_OM_IFACE,
            signal_name="InterfacesAdded",
        )
        self._signal_matches.append(match1)

        # InterfacesRemoved — device goes away
        match2 = self._bus.add_signal_receiver(
            self._on_interfaces_removed,
            dbus_interface=DBUS_OM_IFACE,
            signal_name="InterfacesRemoved",
        )
        self._signal_matches.append(match2)

        # PropertiesChanged — track Connected, State, etc.
        match3 = self._bus.add_signal_receiver(
            self._on_properties_changed,
            dbus_interface=DBUS_PROPS_IFACE,
            signal_name="PropertiesChanged",
            path_keyword="path",
        )
        self._signal_matches.append(match3)

    def _on_interfaces_added(
        self, path: str, interfaces: dict
    ) -> None:
        """Handle a new D-Bus interface appearing (e.g., device connected)."""
        if BLUEZ_DEVICE_IFACE in interfaces:
            props = interfaces[BLUEZ_DEVICE_IFACE]
            name = str(props.get("Name", "Unknown"))
            address = str(props.get("Address", ""))
            connected = bool(props.get("Connected", False))

            if connected:
                GLib.idle_add(
                    self._handle_device_connected, path, name, address
                )

    def _on_interfaces_removed(
        self, path: str, interfaces: list
    ) -> None:
        """Handle a D-Bus interface being removed (e.g., device disconnected)."""
        if BLUEZ_DEVICE_IFACE in interfaces:
            # Find which device this was
            for address, info in list(self._connected_devices.items()):
                if info["path"] == str(path):
                    GLib.idle_add(
                        self._handle_device_disconnected, address
                    )
                    break

    def _on_properties_changed(
        self,
        interface: str,
        changed: dict,
        invalidated: list,
        path: str = "",
    ) -> None:
        """Handle property changes on BlueZ objects."""
        if interface == BLUEZ_DEVICE_IFACE:
            self._handle_device_property_change(path, changed)
        elif interface == BLUEZ_MEDIA_TRANSPORT_IFACE:
            self._handle_transport_property_change(path, changed)

    def _handle_device_property_change(
        self, path: str, changed: dict
    ) -> None:
        """React to Device1 property changes (e.g., Connected toggled)."""
        if "Connected" not in changed:
            return

        connected = bool(changed["Connected"])

        # Get device details
        try:
            device_obj = self._bus.get_object(BLUEZ_SERVICE, path)
            props = dbus.Interface(device_obj, DBUS_PROPS_IFACE)
            name = str(props.Get(BLUEZ_DEVICE_IFACE, "Name"))
            address = str(props.Get(BLUEZ_DEVICE_IFACE, "Address"))
        except dbus.exceptions.DBusException:
            name = "Unknown"
            address = ""

        if connected:
            GLib.idle_add(
                self._handle_device_connected, path, name, address
            )
        else:
            GLib.idle_add(self._handle_device_disconnected, address)

    def _handle_transport_property_change(
        self, path: str, changed: dict
    ) -> None:
        """React to MediaTransport1 property changes (streaming state)."""
        if "State" not in changed:
            return

        state = str(changed["State"])
        logger.info("Media transport state changed: %s", state)

        if state == "active":
            GLib.idle_add(self._handle_streaming_started)
        elif state in ("idle", "pending"):
            GLib.idle_add(self._handle_streaming_stopped)

    # ------------------------------------------------------------------
    # State transitions (called on main thread via GLib.idle_add)
    # ------------------------------------------------------------------

    def _handle_device_connected(
        self, path: str, name: str, address: str
    ) -> bool:
        """Process a device connection event."""
        self._connected_devices[address] = {
            "path": path,
            "name": name,
            "connected": True,
        }
        self.emit("device-connected", name, address)

        if self.is_enabled:
            self._set_state(SpeakerState.CONNECTED)

        logger.info("Device connected: %s (%s)", name, address)
        return False  # Remove from GLib idle

    def _handle_device_disconnected(self, address: str) -> bool:
        """Process a device disconnection event."""
        self._connected_devices.pop(address, None)
        self.emit("device-disconnected", address)

        if self.is_enabled:
            if self._connected_devices:
                self._set_state(SpeakerState.CONNECTED)
            else:
                self._is_streaming = False
                self._set_state(SpeakerState.WAITING)

        logger.info("Device disconnected: %s", address)
        return False

    def _handle_streaming_started(self) -> bool:
        """Process streaming start event."""
        self._is_streaming = True
        self.emit("streaming-started")
        if self.is_enabled:
            self._set_state(SpeakerState.STREAMING)
        logger.info("Audio streaming started.")
        return False

    def _handle_streaming_stopped(self) -> bool:
        """Process streaming stop event."""
        self._is_streaming = False
        self.emit("streaming-stopped")
        if self.is_enabled and self._connected_devices:
            self._set_state(SpeakerState.CONNECTED)
        elif self.is_enabled:
            self._set_state(SpeakerState.WAITING)
        logger.info("Audio streaming stopped.")
        return False

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _set_state(self, new_state: SpeakerState) -> None:
        """Update state and emit the state-changed signal."""
        if self._state != new_state:
            old = self._state
            self._state = new_state
            self.emit("state-changed", new_state.name)
            logger.debug("State: %s → %s", old.name, new_state.name)

    def _emit_error(self, message: str) -> None:
        """Emit an error signal on the main thread."""
        logger.error(message)
        GLib.idle_add(self.emit, "error", message)

    def _register_agent(self) -> None:
        """Create and register the pairing agent."""
        if self._agent:
            return

        self._agent = BluzyAgent(
            self._bus,
            require_confirmation=self._config.require_pairing_confirmation,
            on_confirmation_needed=self._on_confirmation_needed,
        )
        self._agent.register()

    def _unregister_agent(self) -> None:
        """Unregister and destroy the pairing agent."""
        if self._agent:
            self._agent.unregister()
            self._agent = None

    def cleanup(self) -> None:
        """Clean up resources before shutdown."""
        if self.is_enabled:
            self.disable()
        self._unregister_agent()
        logger.info("BluetoothManager cleaned up.")
