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
    """Stable PulseAudio sink and optional port names, suitable for saving."""

    sink: str
    port: Optional[str] = None


@dataclass(frozen=True)
class OutputDevice:
    """One selectable output, including separate speaker/headphone ports."""

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

    Only Bluetooth playback streams are moved; the desktop default and other
    applications' stream assignments are preserved. Ports and device volume
    are shared by all applications using the same physical output.
    """

    def __init__(self, preferred: Optional[OutputTarget] = None) -> None:
        self.preferred = preferred

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
        devices = []
        for sink in sinks:
            name = sink["name"]
            description = sink.get("description") or name
            ports = sink.get("ports") or []
            if not ports:
                devices.append(OutputDevice(OutputTarget(name), description))
            for port in ports:
                label = f"{port.get('description') or port['name']} — {description}"
                if port.get("availability") in ("not available", "no"):
                    label += " (unavailable)"
                devices.append(OutputDevice(OutputTarget(name, port["name"]), label))
        return tuple(sorted(devices, key=lambda device: (device.label, device.target.sink)))

    def _move_bluetooth_streams(self, sink: dict) -> None:
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
        for stream in streams:
            properties = stream.get("properties") or {}
            bluetooth = str(properties.get("node.name", "")).startswith("bluez_input.")
            bluetooth |= str(stream.get("owner_module")) in loopback_modules
            if not bluetooth or str(stream.get("sink")) == str(sink["index"]):
                continue
            try:
                self._run("move-sink-input", str(stream["index"]), sink["name"])
            except OutputError:
                # Streams can end between discovery and moving. Report genuine
                # failures while allowing a phone to stop playback normally.
                remaining = self._list("sink-inputs")
                if any(item["index"] == stream["index"] for item in remaining):
                    raise

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

        target = self.preferred if not missing else None
        if route and target and target.port and selected.get("active_port") != target.port:
            self._run("set-sink-port", selected["name"], target.port)
            # Port changes may restore a different device volume.
            selected = next(s for s in self._list("sinks") if s["name"] == selected["name"])
        if route:
            self._move_bluetooth_streams(selected)
        channels = list((selected.get("volume") or {}).values())
        volume = (
            round(sum(channel["value"] for channel in channels) / len(channels) * 100 / 65536)
            if channels else None
        )
        active_target = OutputTarget(selected["name"], selected.get("active_port") or None)
        label = next(
            (device.label for device in devices if device.target == active_target),
            selected.get("description", selected["name"]),
        )
        return OutputState(devices, self.preferred, selected["name"], label, volume, missing)

    def select(self, target: Optional[OutputTarget], route: bool) -> OutputState:
        """Apply a user choice, preserving the previous preference on failure."""
        sinks = self._list("sinks")
        if target and not any(device.target == target for device in self._devices(sinks)):
            raise OutputError("That output is no longer connected. Choose another output.")
        previous = self.preferred
        changed_sink = next(
            (s for s in sinks if target and s["name"] == target.sink), None
        )
        previous_port = changed_sink.get("active_port") if changed_sink else None
        self.preferred = target
        try:
            if target and target.port:
                self._run("set-sink-port", target.sink, target.port)
            return self.refresh(route)
        except OutputError:
            self.preferred = previous
            if target and target.port and previous_port and previous_port != target.port:
                try:
                    self._run("set-sink-port", target.sink, previous_port)
                except OutputError:
                    logger.warning("Could not restore the previous audio port.")
            raise

    def set_volume(self, sink: str, percent: int) -> None:
        """Change the selected device's volume, without changing the default."""
        self._run("set-sink-volume", sink, f"{max(0, min(100, percent))}%")


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
            port = saved.get("port")
            preferred = OutputTarget(saved["sink"], port if isinstance(port, str) else None)
        self._backend = PulseAudioOutputs(preferred)
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

    def _submit(self, operation, *, save: bool = False) -> None:
        if self._closed:
            return
        self._pending += 1
        future = self._executor.submit(operation)
        future.add_done_callback(lambda done: GLib.idle_add(self._complete, done, save))

    def _complete(self, future, save: bool) -> bool:
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
                    {"sink": target.sink, "port": target.port} if target else None,
                )
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
        sink = self.state.effective_sink
        if sink and not self._closed:
            self._volume_timer = GLib.timeout_add(120, self._apply_volume, sink, percent)

    def _apply_volume(self, sink: str, percent: int) -> bool:
        self._volume_timer = None
        def update():
            self._backend.set_volume(sink, percent)
            return self._backend.refresh()
        self._submit(update)
        return False

    def cleanup(self) -> None:
        """Stop timers and discard worker callbacks after application shutdown."""
        self._closed = True
        for source in (self._timer, self._volume_timer):
            if source is not None:
                GLib.source_remove(source)
        self._timer = self._volume_timer = None
        self._executor.shutdown(wait=False, cancel_futures=True)
