"""Behavioral tests for output routing and its asynchronous GTK boundary."""

import copy
import itertools
import json
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from gi.repository import GLib

from bluzy.audio_outputs import (
    AudioOutputs, OutputError, OutputState, OutputTarget, PulseAudioOutputs,
)
from bluzy.config_manager import ConfigManager
from bluzy.audio_manager import AudioManager


def sink(name="built-in", index=1, ports=True):
    """A realistic pactl JSON output, with ports on one physical sound card."""
    return {
        "index": index, "name": name, "description": name,
        "active_port": "speakers" if ports else None,
        "ports": [
            {"name": "speakers", "description": "Speakers", "availability": "availability unknown"},
            {"name": "headphones", "description": "Headphones", "availability": "not available"},
        ] if ports else [],
        "volume": {"left": {"value": 32768}, "right": {"value": 32768}},
    }


class AudioServer:
    """Stateful server double that exercises real command parsing and routing."""

    def __init__(self):
        self.sinks = [sink(), sink("usb", 2, False)]
        self.default = "built-in"
        self.streams = [
            {"index": 10, "sink": 1, "properties": {"node.name": "bluez_input.phone.0"}},
            {"index": 11, "sink": 1, "properties": {"node.name": "Firefox"}},
        ]
        for stream in self.streams:
            stream["volume"] = {"left": {"value": 65536}, "right": {"value": 65536}}
        self.modules = []
        self.calls = []
        self.fail_move = False
        self.disappear_on_move = False

    def run(self, command, **kwargs):
        self.calls.append(command[1:])
        args = command[1:]
        result = ""
        if args[:2] == ["--format=json", "list"]:
            result = json.dumps({"sinks": self.sinks, "sink-inputs": self.streams, "modules": self.modules}[args[2]])
        elif args == ["get-default-sink"]:
            result = self.default
        elif args[0] == "move-sink-input":
            if self.disappear_on_move:
                self.streams = [stream for stream in self.streams if str(stream["index"]) != args[1]]
            if self.fail_move or self.disappear_on_move:
                return subprocess.CompletedProcess(command, 1, "", "No such entity")
            stream = next(item for item in self.streams if str(item["index"]) == args[1])
            stream["sink"] = next(item["index"] for item in self.sinks if item["name"] == args[2])
        elif args[0] == "set-sink-input-volume":
            selected = next((item for item in self.streams if str(item["index"]) == args[1]), None)
            if selected is None:
                return subprocess.CompletedProcess(command, 1, "", "No such entity")
            value = int(args[2].rstrip("%")) * 65536 / 100
            selected["volume"] = {"left": {"value": value}}
        else:
            raise AssertionError(f"Unexpected audio command: {args}")
        return subprocess.CompletedProcess(command, 0, result, "")


def drain_until(condition, timeout=3):
    """Drive the GLib context until an asynchronous operation completes."""
    deadline = time.monotonic() + timeout
    context = GLib.MainContext.default()
    while not condition():
        if time.monotonic() > deadline:
            raise AssertionError("Timed out waiting for audio operation")
        while context.pending():
            context.iteration(False)
        time.sleep(0.005)


class OutputBackendTests(unittest.TestCase):
    def setUp(self):
        self.server = AudioServer()
        self.patch = patch("bluzy.audio_outputs.subprocess.run", self.server.run)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.backend = PulseAudioOutputs()

    def test_lists_independent_sinks_not_shared_ports(self):
        state = self.backend.refresh()
        self.assertEqual({device.target for device in state.devices}, {
            OutputTarget("built-in"), OutputTarget("usb"),
        })
        self.assertEqual(state.volume, 100)

    def test_select_moves_only_phone_stream_and_preserves_default(self):
        state = self.backend.select(OutputTarget("usb"), route=True)
        self.assertEqual(state.effective_sink, "usb")
        self.assertEqual(self.server.streams[0]["sink"], 2)
        self.assertEqual(self.server.streams[1]["sink"], 1)
        self.assertEqual(self.server.default, "built-in")

    def test_every_default_source_destination_and_other_app_combination(self):
        self.server.sinks.extend([sink("hdmi", 3, False), sink("headset", 4, False)])
        for default, source, destination, other in itertools.product(range(4), repeat=4):
            with self.subTest(default=default, source=source, destination=destination, other=other):
                self.server.default = self.server.sinks[default]["name"]
                self.server.streams[0]["sink"] = self.server.sinks[source]["index"]
                self.server.streams[1]["sink"] = self.server.sinks[other]["index"]
                original_sinks = copy.deepcopy(self.server.sinks)
                original_other = copy.deepcopy(self.server.streams[1])
                original_default = self.server.default
                self.backend.select(OutputTarget(self.server.sinks[destination]["name"]), route=True)
                self.assertEqual(self.server.streams[0]["sink"], self.server.sinks[destination]["index"])
                self.assertEqual(self.server.default, original_default)
                self.assertEqual(self.server.sinks, original_sinks)
                self.assertEqual(self.server.streams[1], original_other)

    def test_selecting_while_idle_never_changes_device_ports_or_volumes(self):
        self.server.streams = []
        original = copy.deepcopy(self.server.sinks)
        for target in (OutputTarget("usb"), OutputTarget("built-in"), None):
            self.backend.select(target, route=False)
            self.assertEqual(self.server.sinks, original)
            self.assertEqual(self.server.default, "built-in")

    def test_saved_selection_routes_new_phone_stream(self):
        self.backend.select(OutputTarget("usb"), route=True)
        self.server.streams.append({"index": 12, "sink": 1, "properties": {"node.name": "bluez_input.reconnected"}})
        self.backend.refresh(route=True)
        self.assertEqual(self.server.streams[-1]["sink"], 2)

    def test_hotplug_fallback_and_restore_keep_preference(self):
        self.backend.select(OutputTarget("usb"), route=True)
        removed = self.server.sinks.pop()
        self.server.streams[0]["sink"] = 1
        state = self.backend.refresh(route=True)
        self.assertTrue(state.missing)
        self.assertEqual(state.effective_sink, "built-in")
        self.assertEqual(state.preferred, OutputTarget("usb"))
        self.server.sinks.append(removed)
        state = self.backend.refresh(route=True)
        self.assertFalse(state.missing)
        self.assertEqual(self.server.streams[0]["sink"], 2)

    def test_system_default_follows_external_changes(self):
        self.server.default = "usb"
        state = self.backend.refresh(route=True)
        self.assertIsNone(state.preferred)
        self.assertEqual(state.effective_sink, "usb")
        self.assertEqual(self.server.streams[0]["sink"], 2)

    def test_return_to_system_default_moves_current_stream(self):
        self.backend.select(OutputTarget("usb"), route=True)
        state = self.backend.select(None, route=True)
        self.assertIsNone(state.preferred)
        self.assertEqual(self.server.streams[0]["sink"], 1)

    def test_volume_changes_only_phone_stream_even_on_a_shared_output(self):
        original_sinks = copy.deepcopy(self.server.sinks)
        original_other = copy.deepcopy(self.server.streams[1])
        self.backend.select(OutputTarget("usb"), route=True)
        self.backend.set_volume(30, route=True)
        self.assertEqual(self.backend.refresh().volume, 30)
        self.assertEqual(round(self.server.streams[0]["volume"]["left"]["value"] * 100 / 65536), 30)
        self.assertEqual(self.server.sinks, original_sinks)
        self.assertEqual(self.server.streams[1], original_other)

    def test_disabled_speaker_mode_does_not_move_streams(self):
        self.backend.select(OutputTarget("usb"), route=False)
        self.backend.refresh(route=False)
        self.assertEqual(self.server.streams[0]["sink"], 1)

    def test_disconnected_selection_fails_without_losing_preference(self):
        self.backend.select(OutputTarget("usb"), route=False)
        with self.assertRaises(OutputError):
            self.backend.select(OutputTarget("gone"), route=True)
        self.assertEqual(self.backend.preferred, OutputTarget("usb"))

    def test_stream_ending_during_switch_is_not_an_error(self):
        self.server.disappear_on_move = True
        self.backend.select(OutputTarget("usb"), route=True)
        self.assertEqual(self.backend.preferred, OutputTarget("usb"))

    def test_failed_stream_move_is_reported_and_preference_reverted(self):
        self.server.fail_move = True
        with self.assertRaises(OutputError):
            self.backend.select(OutputTarget("usb"), route=True)
        self.assertIsNone(self.backend.preferred)

    def test_failed_switch_never_touches_physical_ports(self):
        self.server.streams[0]["sink"] = 2
        self.server.fail_move = True
        original_sinks = copy.deepcopy(self.server.sinks)
        with self.assertRaises(OutputError):
            self.backend.select(OutputTarget("built-in"), route=True)
        self.assertEqual(self.server.sinks, original_sinks)
        self.assertIsNone(self.backend.preferred)

    def test_pulseaudio_bluetooth_loopback_and_unrelated_loopback(self):
        self.server.streams = [
            {"index": 20, "sink": 1, "driver": "module-loopback.c", "owner_module": 7},
            {"index": 21, "sink": 1, "driver": "module-loopback.c", "owner_module": 8},
        ]
        self.server.modules = [
            {"index": 7, "name": "module-loopback", "argument": "source=bluez_source.AA_BB.a2dp_source"},
            {"index": 8, "name": "module-loopback", "argument": "source=alsa_input.mic"},
        ]
        self.backend.select(OutputTarget("usb"), route=True)
        self.assertEqual([stream["sink"] for stream in self.server.streams], [2, 1])

    def test_no_outputs_is_a_valid_state(self):
        self.server.sinks = []
        state = self.backend.refresh(route=True)
        self.assertIsNone(state.effective_sink)
        self.assertEqual(state.devices, ())

    def test_missing_selected_sink_falls_back_to_default(self):
        self.backend.preferred = OutputTarget("removed")
        state = self.backend.refresh(route=True)
        self.assertTrue(state.missing)
        self.assertEqual(state.effective_sink, "built-in")

    def test_missing_pactl_and_invalid_json_are_reported(self):
        with patch("bluzy.audio_outputs.subprocess.run", side_effect=FileNotFoundError):
            with self.assertRaises(OutputError):
                self.backend.refresh()
        with patch("bluzy.audio_outputs.subprocess.run", return_value=subprocess.CompletedProcess([], 0, "invalid", "")):
            with self.assertRaises(OutputError):
                self.backend.refresh()


class OutputAsyncTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config = ConfigManager(self.temp.name)
        self.server = AudioServer()
        self.patch = patch("bluzy.audio_outputs.subprocess.run", self.server.run)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.outputs = AudioOutputs(self.config)
        self.addCleanup(self.outputs.cleanup)

    def wait(self):
        drain_until(lambda: self.outputs._pending == 0)

    def test_save_success_and_restore_on_restart(self):
        self.outputs.select(OutputTarget("usb"))
        self.wait()
        self.assertEqual(self.config.get("audio_output"), {"sink": "usb"})
        restarted = AudioOutputs(ConfigManager(self.temp.name))
        self.addCleanup(restarted.cleanup)
        self.assertEqual(restarted.state.preferred, OutputTarget("usb"))

    def test_failed_selection_does_not_save_and_notifies_ui(self):
        errors = []
        self.outputs.connect("error", lambda _, message: errors.append(message))
        self.outputs.select(OutputTarget("missing"))
        self.wait()
        self.assertIsNone(self.config.get("audio_output"))
        self.assertFalse(self.outputs.selecting)
        self.assertEqual(len(errors), 1)

    def test_slow_audio_service_does_not_block_main_loop_or_queue_polls(self):
        release = threading.Event()
        started = threading.Event()

        def slow_refresh(route):
            started.set()
            release.wait(2)
            return OutputState()

        try:
            with patch.object(self.outputs._backend, "refresh", slow_refresh):
                self.outputs.refresh()
                self.assertTrue(started.wait(1))
                for _ in range(5):
                    self.outputs.refresh()
                self.assertEqual(self.outputs._pending, 1)
                ticks = []
                GLib.idle_add(lambda: ticks.append(True) and False)
                drain_until(lambda: bool(ticks))
                self.assertEqual(self.outputs._pending, 1)
                release.set()
                self.wait()
        finally:
            release.set()

    def test_slider_coalesces_updates_and_refreshes_selected_volume(self):
        self.outputs.routing_enabled = True
        self.outputs.select(OutputTarget("usb"))
        self.wait()
        for percent in range(10, 41):
            self.outputs.set_volume(percent)
        drain_until(lambda: self.outputs._volume_timer is None and self.outputs._pending == 0)
        calls = [call for call in self.server.calls if call[0] == "set-sink-input-volume"]
        self.assertEqual(calls, [["set-sink-input-volume", "10", "40%"]])
        self.assertEqual(self.outputs.state.volume, 40)
        self.assertEqual(self.config.get("bluetooth_volume"), 40)

    def test_migrates_old_port_selection_without_any_shared_mutation(self):
        self.config.set("audio_output", {"sink": "built-in", "port": "headphones"})
        outputs = AudioOutputs(self.config)
        self.addCleanup(outputs.cleanup)
        outputs.routing_enabled = True
        outputs.refresh()
        drain_until(lambda: outputs._pending == 0)
        self.assertEqual(outputs.state.preferred, OutputTarget("built-in"))
        self.assertEqual(self.server.sinks[0]["active_port"], "speakers")

    def test_volume_saved_without_playback_is_applied_to_reconnected_phone(self):
        self.server.streams = []
        self.outputs.set_volume(27)
        drain_until(lambda: self.outputs._volume_timer is None and self.outputs._pending == 0)
        restarted = AudioOutputs(ConfigManager(self.temp.name))
        self.addCleanup(restarted.cleanup)
        self.server.streams = [{"index": 50, "sink": 1, "properties": {"node.name": "bluez_input.new"}}]
        restarted.routing_enabled = True
        restarted.refresh()
        drain_until(lambda: restarted._pending == 0)
        self.assertEqual(restarted.state.volume, 27)
        self.assertIn(["set-sink-input-volume", "50", "27%"], self.server.calls)

    def test_service_failure_recovers_without_repeated_notifications(self):
        errors = []
        self.outputs.connect("error", lambda _, message: errors.append(message))
        with patch.object(self.outputs._backend, "refresh", side_effect=OutputError("offline")):
            self.outputs.refresh()
            self.wait()
            self.outputs.refresh()
            self.wait()
        self.assertFalse(self.outputs.available)
        self.assertEqual(len(errors), 1)
        self.outputs.refresh()
        self.wait()
        self.assertTrue(self.outputs.available)

    def test_old_config_and_malformed_saved_preferences(self):
        for value in (None, "old-format", {"port": 7}, {"sink": 15}):
            self.config.set("audio_output", value)
            outputs = AudioOutputs(self.config)
            self.assertIsNone(outputs.state.preferred)
            outputs.cleanup()

    def test_speaker_mode_lifecycle_never_changes_card_profiles(self):
        audio = AudioManager(self.config)
        self.addCleanup(audio.cleanup)
        audio.start_monitoring()
        drain_until(lambda: audio.outputs._pending == 0)
        audio.stop_monitoring()
        audio.cleanup()
        mutations = [call[0] for call in self.server.calls
                     if call[0] not in ("--format=json", "get-default-sink")]
        self.assertTrue(all(command in ("move-sink-input", "set-sink-input-volume")
                            for command in mutations))


if __name__ == "__main__":
    unittest.main()
