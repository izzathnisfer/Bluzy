# Bluzy — Configuration Manager
# Copyright (c) 2026 Bluzy Contributors
# SPDX-License-Identifier: MIT

"""
Manages persistent user preferences stored in ~/.config/bluzy/config.json.

Thread-safe writes ensure the GTK main loop and background D-Bus callbacks
can both safely update configuration without data races.
"""

import json
import logging
import os
import threading
from pathlib import Path
from typing import Any, Optional

from gi.repository import GLib

logger = logging.getLogger(__name__)

# Default configuration schema
_DEFAULT_CONFIG: dict[str, Any] = {
    "version": 1,
    "speaker_alias": "Bluzy Speaker",
    "autostart": False,
    "first_launch": True,
    "require_pairing_confirmation": True,
    "auto_enable_devices": {},
    # Example entry in auto_enable_devices:
    # "AA:BB:CC:DD:EE:FF": {
    #     "name": "iPhone 15",
    #     "auto_enable": True
    # }
}


class ConfigManager:
    """
    Reads and writes the Bluzy configuration file.

    Configuration is stored as JSON at ``~/.config/bluzy/config.json``.
    All public methods are thread-safe.

    Attributes:
        config_dir: Path to the configuration directory.
        config_path: Path to the configuration file.
    """

    def __init__(self, config_dir: Optional[Path] = None) -> None:
        self._lock = threading.Lock()

        if config_dir is None:
            xdg_config = os.environ.get(
                "XDG_CONFIG_HOME",
                os.path.expanduser("~/.config"),
            )
            self.config_dir = Path(xdg_config) / "bluzy"
        else:
            self.config_dir = Path(config_dir)

        self.config_path = self.config_dir / "config.json"
        self._data: dict[str, Any] = {}
        self._load()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get(self, key: str, default: Any = None) -> Any:
        """Retrieve a top-level configuration value."""
        with self._lock:
            return self._data.get(key, default)

    def set(self, key: str, value: Any, *, save: bool = True) -> None:
        """Set a top-level configuration value and optionally persist."""
        with self._lock:
            self._data[key] = value
        if save:
            self.save()

    @property
    def speaker_alias(self) -> str:
        return self.get("speaker_alias", _DEFAULT_CONFIG["speaker_alias"])

    @speaker_alias.setter
    def speaker_alias(self, value: str) -> None:
        self.set("speaker_alias", value)

    @property
    def autostart(self) -> bool:
        return self.get("autostart", False)

    @autostart.setter
    def autostart(self, value: bool) -> None:
        self.set("autostart", value)

    @property
    def first_launch(self) -> bool:
        return self.get("first_launch", True)

    @first_launch.setter
    def first_launch(self, value: bool) -> None:
        self.set("first_launch", value)

    @property
    def require_pairing_confirmation(self) -> bool:
        return self.get("require_pairing_confirmation", True)

    @require_pairing_confirmation.setter
    def require_pairing_confirmation(self, value: bool) -> None:
        self.set("require_pairing_confirmation", value)

    # ------------------------------------------------------------------
    # Device auto-enable management
    # ------------------------------------------------------------------

    def get_device_config(self, mac: str) -> Optional[dict[str, Any]]:
        """Return config for a specific device by MAC address, or None."""
        with self._lock:
            devices = self._data.get("auto_enable_devices", {})
            return devices.get(mac)

    def set_device_auto_enable(
        self, mac: str, name: str, auto_enable: bool
    ) -> None:
        """Set/update auto-enable preference for a device."""
        with self._lock:
            devices = self._data.setdefault("auto_enable_devices", {})
            devices[mac] = {"name": name, "auto_enable": auto_enable}
        self.save()

    def remove_device(self, mac: str) -> None:
        """Remove a device from the auto-enable list."""
        with self._lock:
            devices = self._data.get("auto_enable_devices", {})
            devices.pop(mac, None)
        self.save()

    def get_all_devices(self) -> dict[str, dict[str, Any]]:
        """Return a copy of all device configurations."""
        with self._lock:
            return dict(self._data.get("auto_enable_devices", {}))

    def is_auto_enable(self, mac: str) -> bool:
        """Check if a device is configured for auto-enable."""
        cfg = self.get_device_config(mac)
        return cfg.get("auto_enable", False) if cfg else False

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save(self) -> None:
        """
        Persist configuration to disk.

        Uses ``GLib.idle_add`` to ensure the actual file write happens on
        the main thread if called from a background thread. Falls back to
        direct write if GLib main loop is not running.
        """
        try:
            GLib.idle_add(self._write_to_disk)
        except Exception:
            # GLib main loop may not be running (e.g., during tests)
            self._write_to_disk()

    def reload(self) -> None:
        """Re-read configuration from disk."""
        self._load()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _load(self) -> None:
        """Load configuration from disk, applying defaults for missing keys."""
        with self._lock:
            if self.config_path.exists():
                try:
                    raw = self.config_path.read_text(encoding="utf-8")
                    self._data = json.loads(raw)
                    logger.info("Configuration loaded from %s", self.config_path)
                except (json.JSONDecodeError, OSError) as exc:
                    logger.warning(
                        "Failed to read config (%s); using defaults.", exc
                    )
                    self._data = dict(_DEFAULT_CONFIG)
            else:
                logger.info("No config file found; using defaults.")
                self._data = dict(_DEFAULT_CONFIG)

            # Merge any missing default keys (forward-compatible)
            for key, value in _DEFAULT_CONFIG.items():
                self._data.setdefault(key, value)

    def _write_to_disk(self) -> bool:
        """Write current config to disk. Returns False to remove GLib idle source."""
        with self._lock:
            try:
                self.config_dir.mkdir(parents=True, exist_ok=True)
                tmp_path = self.config_path.with_suffix(".tmp")
                tmp_path.write_text(
                    json.dumps(self._data, indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )
                tmp_path.replace(self.config_path)
                logger.debug("Configuration saved to %s", self.config_path)
            except OSError as exc:
                logger.error("Failed to save config: %s", exc)
        return False  # Remove from GLib idle source
