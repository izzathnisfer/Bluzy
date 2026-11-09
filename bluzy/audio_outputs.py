# Bluzy — Audio output selection
# Copyright (c) 2026 Bluzy Contributors
# SPDX-License-Identifier: MIT

"""Discover output devices and route Bluetooth playback without blocking GTK."""

import json
import logging
import shlex
import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Optional

from gi.repository import GLib, GObject

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class OutputTarget:
    """An independently routable output, identified by its stable sink name."""

    sink: str


@dataclass(frozen=True)
class OutputDevice:
    """One independently routable output, as in VLC's PulseAudio device list."""

    target: OutputTarget
    label: str


@dataclass(frozen=True)
class OutputState:
    """An audio-server snapshot delivered to GTK on its main thread."""

    devices: tuple[OutputDevice, ...] = ()
    preferred: Optional[OutputTarget] = None
    effective_sink: Optional[str] = None
    effective_label: str = "No audio outputs available"
    volume: Optional[int] = None
    missing: bool = False


class OutputError(RuntimeError):
    """An audio-server operation could not be completed."""


class PulseAudioOutputs:
    """Synchronous pactl backend, used exclusively by a single worker thread.

    Enumerate sinks and move only Bluetooth playback streams, matching VLC's
    PulseAudio StreamMove behavior. Never change shared ports, card profiles,
    device volumes, or the desktop default. A port on a sink is not a separate
    stream destination and must not be presented as one.
    """

    def __init__(
        self, preferred: Optional[OutputTarget] = None, volume: int = 100
    ) -> None:
        self.preferred = preferred
        self.volume = max(0, min(100, volume))

    def _run(self, *args: str) -> str:
        try:
            result = subprocess.run(
                ["pactl", *args], capture_output=True, text=True,
                timeout=5,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise OutputError(
                "Cannot reach the audio service. Check PipeWire/PulseAudio."
            ) from exc
        if result.returncode:
            raise OutputError(result.stderr.strip() or "Audio service command failed")
        return result.stdout.strip()

    def _list(self, kind: str) -> list[dict]:
        try:
            result = json.loads(self._run("--format=json", "list", kind))
        except (ValueError, TypeError) as exc:
            raise OutputError("The audio service returned an invalid device list.") from exc
        if not isinstance(result, list):
            raise OutputError("The audio service returned an invalid device list.")
        return result

    @staticmethod
    def _devices(sinks: list[dict]) -> tuple[OutputDevice, ...]:
        devices = [
            OutputDevice(OutputTarget(sink["name"]), sink.get("description") or sink["name"])
            for sink in sinks
        ]
        return tuple(sorted(devices, key=lambda device: (device.label, device.target.sink)))

    def _bluetooth_streams(self) -> list[dict]:
        """Identify incoming phone playback without claiming other apps' audio."""
        streams = self._list("sink-inputs")
        # PulseAudio's module-loopback identifies its capture source in module
        # arguments rather than in the playback stream properties.
        loopback_modules = set()
        if any(stream.get("driver") == "module-loopback.c" for stream in streams):
            for module in self._list("modules"):
                if module.get("name") != "module-loopback":
                    continue
                try:
                    args = shlex.split(module.get("argument", ""))
                except ValueError:
                    continue
                if any(
                    arg.startswith(("source=bluez_source.", "source=bluez_input."))
                    for arg in args
                ):
                    loopback_modules.add(str(module["index"]))
        bluetooth_streams = []
        for stream in streams:
            properties = stream.get("properties") or {}
            bluetooth = str(properties.get("node.name", "")).startswith("bluez_input.")
            bluetooth |= str(stream.get("owner_module")) in loopback_modules
            if bluetooth:
                bluetooth_streams.append(stream)
        return bluetooth_streams

    def _stream_command(self, stream: dict, *args: str) -> None:
        """Ignore only streams that ended while a command was in flight."""
        try:
            self._run(*args)
        except OutputError:
            remaining = self._list("sink-inputs")
            if any(item["index"] == stream["index"] for item in remaining):
                raise

    def _route_streams(self, sink: dict) -> None:
        for stream in self._bluetooth_streams():
            index = str(stream["index"])
            if str(stream.get("sink")) != str(sink["index"]):
                self._stream_command(stream, "move-sink-input", index, sink["name"])
            self._set_stream_volume(stream)

    def _set_stream_volume(self, stream: dict) -> None:
        channels = list((stream.get("volume") or {}).values())
        if channels and all(
            round(channel["value"] * 100 / 65536) == self.volume
            for channel in channels
        ):
            return
        self._stream_command(
            stream, "set-sink-input-volume", str(stream["index"]), f"{self.volume}%"
        )

    def refresh(self, route: bool = False) -> OutputState:
        """Refresh devices and optionally maintain the Bluetooth playback route."""
        sinks = self._list("sinks")
        devices = self._devices(sinks)
        default = self._run("get-default-sink") if sinks else None
        selected = next(
            (s for s in sinks if self.preferred and s["name"] == self.preferred.sink),
            None,
        )
        missing = self.preferred is not None and not any(
            device.target == self.preferred for device in devices
        )
        if missing or self.preferred is None:
            selected = next((s for s in sinks if s["name"] == default), None)
        if selected is None and sinks:
            selected = sinks[0]
        if selected is None:
            return OutputState(devices=devices, preferred=self.preferred, missing=missing)

        if route:
            self._route_streams(selected)
        return OutputState(
            devices, self.preferred, selected["name"],
            selected.get("description", selected["name"]), self.volume, missing,
        )

    def select(self, target: Optional[OutputTarget], route: bool) -> OutputState:
        """Apply a user choice, preserving the previous preference on failure."""
        sinks = self._list("sinks")
        if target and not any(device.target == target for device in self._devices(sinks)):
            raise OutputError("That output is no longer connected. Choose another output.")
        previous = self.preferred
        self.preferred = target
        try:
            return self.refresh(route)
        except OutputError:
            self.preferred = previous
            raise

    def set_volume(self, percent: int, route: bool = False) -> None:
        """Change only incoming Bluetooth stream volume, like VLC's VolumeSet."""
        previous = self.volume
        self.volume = max(0, min(100, percent))
        try:
            if route:
                for stream in self._bluetooth_streams():
                    self._set_stream_volume(stream)
        except OutputError:
            self.volume = previous
            raise


class AudioOutputs(GObject.Object):
    """Asynchronous output discovery/routing and saved selection for the UI."""

    __gsignals__ = {
        "changed": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "error": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
    }

    def __init__(self, config) -> None:
        super().__init__()
        self._config = config
        saved = config.get("audio_output") or {}
        preferred = None
        if isinstance(saved, dict) and isinstance(saved.get("sink"), str):
            # Migrate 2.0.0 preferences by keeping the sink and ignoring its
            # shared port. Never reapply that old port choice.
            preferred = OutputTarget(saved["sink"])
        volume = config.get("bluetooth_volume", 100)
        if not isinstance(volume, int):
            volume = 100
        self._backend = PulseAudioOutputs(preferred, volume)
        self.state = OutputState(preferred=preferred)
        self.selecting = False
        self.available = False
        self.routing_enabled = False
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="bluzy-output")
        self._pending = 0
        self._closed = False
        self._timer = None
        self._volume_timer = None
        self._last_error = None

    def start(self) -> None:
        """Discover outputs even while speaker mode is off or minimized."""
        if self._timer is None and not self._closed:
            self.refresh()
            self._timer = GLib.timeout_add_seconds(2, self.refresh)

    def _submit(self, operation, *, save: bool = False, save_volume: bool = False) -> None:
        if self._closed:
            return
        self._pending += 1
        future = self._executor.submit(operation)
        future.add_done_callback(
            lambda done: GLib.idle_add(self._complete, done, save, save_volume)
        )

    def _complete(self, future, save: bool, save_volume: bool) -> bool:
        self._pending -= 1
        if self._closed:
            return False
        try:
            state = future.result()
            if state is not None:
                self.state = state
                self.available = True
            if save:
                target = self.state.preferred
                self._config.set(
                    "audio_output",
                    {"sink": target.sink} if target else None,
                )
            if save_volume:
                self._config.set("bluetooth_volume", self.state.volume)
            self._last_error = None
        except Exception as exc:
            logger.warning("Audio output operation failed: %s", exc)
            message = str(exc)
            if message != self._last_error:
                self.emit("error", f"Could not update audio output: {message}")
                self._last_error = message
            if not save:
                self.available = False
        finally:
            if save:
                self.selecting = False
            self.emit("changed")
        return False

    def refresh(self) -> bool:
        """Poll without building a backlog when the audio server is slow."""
        if not self._closed and not self._pending:
            route = self.routing_enabled
            self._submit(lambda: self._backend.refresh(route))
        return not self._closed

    def select(self, target: Optional[OutputTarget]) -> None:
        """Switch output on the worker and save only a successful selection."""
        if self.selecting or self._closed:
            return
        self.selecting = True
        route = self.routing_enabled
        self._submit(lambda: self._backend.select(target, route), save=True)
        self.emit("changed")

    def set_volume(self, percent: int) -> None:
        """Coalesce slider updates; never run pactl on the GTK main thread."""
        if self._volume_timer is not None:
            GLib.source_remove(self._volume_timer)
        if not self._closed:
            self._volume_timer = GLib.timeout_add(120, self._apply_volume, percent)

    def _apply_volume(self, percent: int) -> bool:
        self._volume_timer = None
        route = self.routing_enabled

        def update():
            self._backend.set_volume(percent, route)
            return self._backend.refresh()

        self._submit(update, save_volume=True)
        return False

    def cleanup(self) -> None:
        """Stop timers and discard worker callbacks after application shutdown."""
        self._closed = True
        for source in (self._timer, self._volume_timer):
            if source is not None:
                GLib.source_remove(source)
        self._timer = self._volume_timer = None
        self._executor.shutdown(wait=False, cancel_futures=True)
