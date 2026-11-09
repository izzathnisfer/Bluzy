"""GTK smoke tests; skipped when no display is available."""

import tempfile
import unittest
from unittest.mock import patch

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gtk

from bluzy.audio_manager import AudioManager
from bluzy.audio_outputs import OutputTarget
from bluzy.bluetooth_manager import BluetoothManager
from bluzy.config_manager import ConfigManager
from bluzy.window import BluzyWindow
from test_audio_outputs import AudioServer, drain_until


class OutputWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not Gtk.init_check() or Gdk.Display.get_default() is None:
            raise unittest.SkipTest("No GTK display available")
        Adw.init()
        cls.app = Adw.Application(application_id="com.github.izzathnisfer.bluzy.OutputTests")
        cls.app.register()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config = ConfigManager(self.temp.name)
        self.config.first_launch = False
        self.server = AudioServer()
        self.pactl_patch = patch("bluzy.audio_outputs.subprocess.run", self.server.run)
        self.pactl_patch.start()
        self.addCleanup(self.pactl_patch.stop)
        self.audio = AudioManager(self.config)
        self.addCleanup(self.audio.outputs.cleanup)
        self.window = BluzyWindow(self.app, BluetoothManager(self.config), self.audio, self.config)
        self.addCleanup(self.window.destroy)
        drain_until(lambda: self.audio.outputs.available)

    def test_dropdown_switches_output_and_volume_targets_selection(self):
        target = OutputTarget("usb")
        index = self.window._output_targets.index(target)
        self.window._output_row.set_selected(index)
        drain_until(lambda: not self.audio.outputs.selecting)
        self.assertEqual(self.audio.outputs.state.preferred, target)
        self.assertEqual(self.window._output_row.get_selected(), index)
        self.window._volume_scale.set_value(35)
        drain_until(lambda: self.audio.outputs._volume_timer is None and self.audio.outputs._pending == 0)
        self.assertIn(["set-sink-volume", "usb", "35%"], self.server.calls)

    def test_hotplug_refresh_never_selects_another_device_by_index(self):
        self.audio.outputs.select(OutputTarget("usb"))
        drain_until(lambda: not self.audio.outputs.selecting)
        self.server.sinks.pop()
        self.audio.outputs.refresh()
        drain_until(lambda: self.audio.outputs._pending == 0)
        index = self.window._output_row.get_selected()
        self.assertEqual(self.window._output_targets[index], OutputTarget("usb"))
        self.assertIn("disconnected", self.window._output_row.get_subtitle())
        self.assertEqual(self.config.get("audio_output")["sink"], "usb")


if __name__ == "__main__":
    unittest.main()
