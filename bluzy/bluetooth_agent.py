# Bluzy — Bluetooth Agent
# Copyright (c) 2026 Bluzy Contributors
# SPDX-License-Identifier: MIT

"""
BlueZ Agent1 D-Bus implementation for automated pairing management.

This agent handles incoming pairing and connection authorization requests.
Supports two modes:

- **Auto-accept** (require_confirmation=False): all pairing requests are
  accepted silently — convenient but less secure.
- **Confirmation required** (require_confirmation=True): the agent stores
  D-Bus async reply callbacks and emits a signal so the UI can prompt the
  user — the reply is sent later without blocking the main loop.

The agent registers on the system bus at ``/com/github/bluzy/agent`` and
sets itself as the default agent via ``AgentManager1.RequestDefaultAgent``.
"""

import logging
from typing import Callable, Optional

import dbus
import dbus.service

from bluzy.utils import (
    BLUEZ_AGENT_IFACE,
    BLUEZ_AGENT_MANAGER_IFACE,
    BLUEZ_SERVICE,
    DBUS_PROPS_IFACE,
)

logger = logging.getLogger(__name__)

AGENT_PATH = "/com/github/bluzy/agent"
AGENT_CAPABILITY = "NoInputNoOutput"


class BluzyAgent(dbus.service.Object):
    """
    D-Bus object implementing ``org.bluez.Agent1``.

    Uses D-Bus async callbacks for ``RequestConfirmation`` so the GLib
    main loop is never blocked while waiting for user input.

    Args:
        bus: The system D-Bus connection.
        require_confirmation: If True, pairing requests are deferred
            until the UI responds via ``complete_confirmation()``.
        on_confirmation_needed: Optional callable receiving
            ``(device_path: str, device_name: str, passkey: int)``.
            Called on the main thread to prompt the user.
    """

    def __init__(
        self,
        bus: dbus.SystemBus,
        require_confirmation: bool = True,
        on_confirmation_needed: Optional[
            Callable[[str, str, int], None]
        ] = None,
    ) -> None:
        super().__init__(bus, AGENT_PATH)
        self._bus = bus
        self.require_confirmation = require_confirmation
        self.on_confirmation_needed = on_confirmation_needed

        # Stored async reply callbacks for pending confirmation
        self._pending_reply: Optional[Callable] = None
        self._pending_error: Optional[Callable] = None

        logger.info(
            "BluzyAgent initialized (require_confirmation=%s)",
            require_confirmation,
        )

    # ------------------------------------------------------------------
    # Async confirmation response (called from the UI)
    # ------------------------------------------------------------------

    def complete_confirmation(self, accepted: bool) -> None:
        """
        Complete a pending pairing confirmation.

        Called by the UI (via BluetoothManager) after the user accepts
        or rejects. This sends the deferred D-Bus reply.
        """
        if accepted and self._pending_reply:
            logger.info("Pairing accepted via UI.")
            self._pending_reply()
        elif not accepted and self._pending_error:
            logger.info("Pairing rejected via UI.")
            self._pending_error(
                dbus.exceptions.DBusException(
                    "org.bluez.Error.Rejected",
                    "Pairing rejected by user.",
                )
            )
        else:
            logger.warning(
                "complete_confirmation called but no pending request."
            )

        self._pending_reply = None
        self._pending_error = None

    # ------------------------------------------------------------------
    # Agent1 interface methods
    # ------------------------------------------------------------------

    @dbus.service.method(
        BLUEZ_AGENT_IFACE, in_signature="", out_signature=""
    )
    def Release(self) -> None:
        """Called when the agent is unregistered."""
        logger.info("Agent released.")

    @dbus.service.method(
        BLUEZ_AGENT_IFACE, in_signature="os", out_signature=""
    )
    def AuthorizeService(self, device: str, uuid: str) -> None:
        """
        Authorize a service connection from a remote device.

        Always accepts A2DP-related services since that is our purpose.
        """
        device_name = self._get_device_name(device)
        logger.info(
            "AuthorizeService: device=%s (%s), uuid=%s",
            device_name,
            device,
            uuid,
        )
        # Auto-trust the device for future connections
        self._trust_device(device)

    @dbus.service.method(
        BLUEZ_AGENT_IFACE, in_signature="o", out_signature="s"
    )
    def RequestPinCode(self, device: str) -> str:
        """Provide a PIN code for legacy pairing."""
        device_name = self._get_device_name(device)
        logger.info("RequestPinCode: device=%s (%s)", device_name, device)
        self._trust_device(device)
        return "0000"

    @dbus.service.method(
        BLUEZ_AGENT_IFACE, in_signature="o", out_signature="u"
    )
    def RequestPasskey(self, device: str) -> dbus.UInt32:
        """Provide a numeric passkey for SSP pairing."""
        device_name = self._get_device_name(device)
        logger.info("RequestPasskey: device=%s (%s)", device_name, device)
        self._trust_device(device)
        return dbus.UInt32(0)

    @dbus.service.method(
        BLUEZ_AGENT_IFACE, in_signature="ouq", out_signature=""
    )
    def DisplayPasskey(
        self, device: str, passkey: dbus.UInt32, entered: dbus.UInt16
    ) -> None:
        """Display a passkey for the user to enter on the remote device."""
        logger.info(
            "DisplayPasskey: device=%s, passkey=%06d, entered=%d",
            device,
            passkey,
            entered,
        )

    @dbus.service.method(
        BLUEZ_AGENT_IFACE, in_signature="os", out_signature=""
    )
    def DisplayPinCode(self, device: str, pincode: str) -> None:
        """Display a PIN code for the user."""
        logger.info("DisplayPinCode: device=%s, pin=%s", device, pincode)

    @dbus.service.method(
        BLUEZ_AGENT_IFACE,
        in_signature="ou",
        out_signature="",
        async_callbacks=("reply_handler", "error_handler"),
    )
    def RequestConfirmation(
        self,
        device: str,
        passkey: dbus.UInt32,
        reply_handler: Callable,
        error_handler: Callable,
    ) -> None:
        """
        Confirm a passkey for pairing.

        Uses D-Bus async callbacks so the main loop is NOT blocked.
        If confirmation is required, the reply is deferred until the
        UI calls ``complete_confirmation()``.
        """
        device_name = self._get_device_name(device)
        logger.info(
            "RequestConfirmation: device=%s (%s), passkey=%06d",
            device_name,
            device,
            passkey,
        )

        if not self.require_confirmation or not self.on_confirmation_needed:
            # Auto-accept
            logger.info("Auto-accepting pairing for %s", device_name)
            self._trust_device(device)
            reply_handler()
            return

        # Store the async callbacks — the UI will call complete_confirmation()
        self._pending_reply = lambda: (
            self._trust_device(device),
            reply_handler(),
        )
        self._pending_error = error_handler

        # Notify the UI (non-blocking)
        logger.info(
            "Waiting for UI confirmation for %s (passkey %06d)",
            device_name,
            passkey,
        )
        self.on_confirmation_needed(
            str(device), device_name, int(passkey)
        )

    @dbus.service.method(
        BLUEZ_AGENT_IFACE, in_signature="o", out_signature=""
    )
    def RequestAuthorization(self, device: str) -> None:
        """Authorize an incoming connection from a device."""
        device_name = self._get_device_name(device)
        logger.info(
            "RequestAuthorization: device=%s (%s)", device_name, device
        )
        self._trust_device(device)

    @dbus.service.method(
        BLUEZ_AGENT_IFACE, in_signature="", out_signature=""
    )
    def Cancel(self) -> None:
        """Cancel an ongoing pairing request."""
        logger.info("Agent pairing cancelled.")
        # Clean up any pending confirmation
        self._pending_reply = None
        self._pending_error = None

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(self) -> bool:
        """
        Register this agent with BlueZ and request to be the default agent.

        Returns:
            True if registration succeeded.
        """
        try:
            obj = self._bus.get_object(BLUEZ_SERVICE, "/org/bluez")
            manager = dbus.Interface(obj, BLUEZ_AGENT_MANAGER_IFACE)
            manager.RegisterAgent(AGENT_PATH, AGENT_CAPABILITY)
            manager.RequestDefaultAgent(AGENT_PATH)
            logger.info("Agent registered as default at %s", AGENT_PATH)
            return True
        except dbus.exceptions.DBusException as exc:
            logger.error("Failed to register agent: %s", exc)
            return False

    def unregister(self) -> None:
        """Unregister the agent from BlueZ and release the D-Bus object path."""
        try:
            obj = self._bus.get_object(BLUEZ_SERVICE, "/org/bluez")
            manager = dbus.Interface(obj, BLUEZ_AGENT_MANAGER_IFACE)
            manager.UnregisterAgent(AGENT_PATH)
            logger.info("Agent unregistered from BlueZ.")
        except dbus.exceptions.DBusException as exc:
            logger.warning("Failed to unregister agent: %s", exc)

        # Release the D-Bus object path so a new agent can be created later
        try:
            self.remove_from_connection()
            logger.debug("Agent D-Bus object path released.")
        except Exception as exc:
            logger.debug("Could not remove agent from connection: %s", exc)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _trust_device(self, device_path: str) -> None:
        """Set the Trusted property to True on a device."""
        try:
            device_obj = self._bus.get_object(BLUEZ_SERVICE, device_path)
            props = dbus.Interface(device_obj, DBUS_PROPS_IFACE)
            props.Set("org.bluez.Device1", "Trusted", dbus.Boolean(True))
            logger.info("Device trusted: %s", device_path)
        except dbus.exceptions.DBusException as exc:
            logger.warning("Failed to trust device %s: %s", device_path, exc)

    def _get_device_name(self, device_path: str) -> str:
        """Retrieve the friendly name of a device."""
        try:
            device_obj = self._bus.get_object(BLUEZ_SERVICE, device_path)
            props = dbus.Interface(device_obj, DBUS_PROPS_IFACE)
            name = props.Get("org.bluez.Device1", "Name")
            return str(name)
        except dbus.exceptions.DBusException:
            return "Unknown"
