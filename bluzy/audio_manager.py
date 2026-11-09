# Bluzy — Audio Manager
# Copyright (c) 2026 Bluzy Contributors
# SPDX-License-Identifier: MIT

"""Coordinate incoming Bluetooth playback without changing shared devices.

PipeWire/WirePlumber or PulseAudio negotiate Bluetooth audio profiles. Bluzy
only routes the incoming playback streams; scanning every Bluetooth card and
forcing/resetting its profile can disrupt unrelated headphones and speakers.
"""

from typing import Optional

from gi.repository import GObject

from bluzy.audio_outputs import AudioOutputs
from bluzy.config_manager import ConfigManager


class AudioManager(GObject.Object):
    """Manage Bluzy's playback route and per-stream volume asynchronously."""

    __gsignals__ = {
        "error": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
    }

    def __init__(self, config: ConfigManager) -> None:
        super().__init__()
        self.outputs = AudioOutputs(config)

    def get_volume(self) -> Optional[int]:
        """Return Bluzy's own playback volume, independent of device volume."""
        return self.outputs.state.volume

    def set_volume(self, percent: int) -> bool:
        """Queue a Bluetooth playback volume change without blocking GTK."""
        self.outputs.set_volume(percent)
        return True

    def start_monitoring(self) -> None:
        """Maintain the playback route while speaker mode is enabled."""
        self.outputs.routing_enabled = True
        self.outputs.start()
        self.outputs.refresh()

    def stop_monitoring(self) -> None:
        """Stop routing phone playback without changing any card profiles."""
        self.outputs.routing_enabled = False

    def cleanup(self) -> None:
        """Stop workers and timers, leaving audio-device settings alone."""
        self.stop_monitoring()
        self.outputs.cleanup()
