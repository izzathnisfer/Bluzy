# Bluzy — Utility Functions
# Copyright (c) 2026 Bluzy Contributors
# SPDX-License-Identifier: MIT

"""
System-level checks and helper functions.

Provides pre-flight checks for Bluetooth adapter availability,
bluetooth group membership, and service status.
"""

import grp
import logging
import os
import subprocess
from typing import Optional

import dbus

logger = logging.getLogger(__name__)

# BlueZ D-Bus constants
BLUEZ_SERVICE = "org.bluez"
BLUEZ_ADAPTER_IFACE = "org.bluez.Adapter1"
BLUEZ_DEVICE_IFACE = "org.bluez.Device1"
BLUEZ_MEDIA_TRANSPORT_IFACE = "org.bluez.MediaTransport1"
BLUEZ_AGENT_IFACE = "org.bluez.Agent1"
BLUEZ_AGENT_MANAGER_IFACE = "org.bluez.AgentManager1"
DBUS_OM_IFACE = "org.freedesktop.DBus.ObjectManager"
DBUS_PROPS_IFACE = "org.freedesktop.DBus.Properties"

# A2DP UUIDs
A2DP_SINK_UUID = "0000110b-0000-1000-8000-00805f9b34fb"
A2DP_SOURCE_UUID = "0000110a-0000-1000-8000-00805f9b34fb"


def check_bluetooth_group() -> bool:
    """
    Check if the current user is a member of the 'bluetooth' group.

    Returns:
        True if the user is in the bluetooth group, False otherwise.
    """
    try:
        bt_group = grp.getgrnam("bluetooth")
        username = os.environ.get("USER", os.getlogin())
        return username in bt_group.gr_mem
    except KeyError:
        # 'bluetooth' group does not exist on this system
        logger.warning("The 'bluetooth' group does not exist on this system.")
        return True  # Don't block the user if the group doesn't exist
    except OSError as exc:
        logger.warning("Could not check bluetooth group membership: %s", exc)
        return True


def check_bluetooth_service() -> bool:
    """
    Check if the bluetooth systemd service is active.

    Returns:
        True if the bluetooth service is running.
    """
    try:
        result = subprocess.run(
            ["systemctl", "is-active", "bluetooth"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        is_active = result.stdout.strip() == "active"
        if not is_active:
            logger.warning("Bluetooth service is not active.")
        return is_active
    except (subprocess.SubprocessError, FileNotFoundError) as exc:
        logger.error("Failed to check bluetooth service: %s", exc)
        return False


def get_system_bus() -> dbus.SystemBus:
    """Get the D-Bus system bus."""
    return dbus.SystemBus()


def get_session_bus() -> dbus.SessionBus:
    """Get the D-Bus session bus."""
    return dbus.SessionBus()


def get_managed_objects(bus: dbus.SystemBus) -> dict:
    """
    Retrieve all managed objects from BlueZ.

    Returns:
        Dictionary of object paths → interfaces → properties.
    """
    try:
        obj = bus.get_object(BLUEZ_SERVICE, "/")
        manager = dbus.Interface(obj, DBUS_OM_IFACE)
        return manager.GetManagedObjects()
    except dbus.exceptions.DBusException as exc:
        logger.error("Failed to get BlueZ managed objects: %s", exc)
        return {}


def get_adapter_path(bus: dbus.SystemBus) -> Optional[str]:
    """
    Find the first available Bluetooth adapter object path.

    Returns:
        Object path string (e.g. '/org/bluez/hci0') or None.
    """
    objects = get_managed_objects(bus)
    for path, interfaces in objects.items():
        if BLUEZ_ADAPTER_IFACE in interfaces:
            logger.info("Found Bluetooth adapter at %s", path)
            return str(path)

    logger.warning("No Bluetooth adapter found.")
    return None


def check_adapter_exists(bus: dbus.SystemBus) -> bool:
    """Check whether at least one Bluetooth adapter exists."""
    return get_adapter_path(bus) is not None


def get_connected_devices(bus: dbus.SystemBus) -> list[dict]:
    """
    List all currently connected Bluetooth devices.

    Returns:
        List of dicts with 'path', 'name', 'address', 'connected' keys.
    """
    devices = []
    objects = get_managed_objects(bus)

    for path, interfaces in objects.items():
        if BLUEZ_DEVICE_IFACE not in interfaces:
            continue

        props = interfaces[BLUEZ_DEVICE_IFACE]
        connected = bool(props.get("Connected", False))
        if connected:
            devices.append({
                "path": str(path),
                "name": str(props.get("Name", "Unknown")),
                "address": str(props.get("Address", "")),
                "connected": connected,
            })

    return devices


def mac_to_path_segment(mac: str) -> str:
    """Convert a MAC address 'AA:BB:CC:DD:EE:FF' to 'AA_BB_CC_DD_EE_FF'."""
    return mac.replace(":", "_")


def path_segment_to_mac(segment: str) -> str:
    """Convert 'AA_BB_CC_DD_EE_FF' back to 'AA:BB:CC:DD:EE:FF'."""
    return segment.replace("_", ":")


def format_device_info(name: str, address: str) -> str:
    """Format device name and address for display."""
    if name and name != address:
        return f"{name} ({address})"
    return address
