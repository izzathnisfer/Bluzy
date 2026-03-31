# Bluzy — Main Application Window
# Copyright (c) 2026 Bluzy Contributors
# SPDX-License-Identifier: MIT

"""
Primary UI window built with GTK4 + Libadwaita.

Layout:
    AdwApplicationWindow
    └── AdwToastOverlay
        └── AdwToolbarView
            ├── AdwHeaderBar
            └── GtkScrolledWindow
                └── AdwClamp
                    └── GtkBox (vertical)
                        ├── Hero section (icon + title)
                        ├── Speaker mode toggle (AdwSwitchRow)
                        ├── Status indicator (animated dot + label)
                        ├── Connected devices list (AdwPreferencesGroup)
                        ├── Settings group (alias, confirmation, autostart)
                        └── Volume control slider

All state changes are driven by signals from BluetoothManager/AudioManager.
"""

import logging
import os
from pathlib import Path
from typing import Optional

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gio, GLib, Gtk

from bluzy.bluetooth_manager import BluetoothManager, SpeakerState
from bluzy.audio_manager import AudioManager
from bluzy.config_manager import ConfigManager

logger = logging.getLogger(__name__)


class BluzyWindow(Adw.ApplicationWindow):
    """Main Bluzy application window."""

    def __init__(
        self,
        app: Adw.Application,
        bt_manager: BluetoothManager,
        audio_manager: AudioManager,
        config: ConfigManager,
    ) -> None:
        super().__init__(
            application=app,
            title="Bluzy",
            default_width=420,
            default_height=580,
            resizable=False,
        )

        self._bt = bt_manager
        self._audio = audio_manager
        self._config = config
        self._toast_overlay: Optional[Adw.ToastOverlay] = None
        self._switch_row: Optional[Adw.SwitchRow] = None
        self._status_label: Optional[Gtk.Label] = None
        self._status_dot: Optional[Gtk.DrawingArea] = None
        self._devices_group: Optional[Adw.PreferencesGroup] = None
        self._device_rows: dict[str, Adw.ActionRow] = {}
        self._alias_entry: Optional[Adw.EntryRow] = None
        self._volume_scale: Optional[Gtk.Scale] = None
        self._suppress_switch_signal = False

        self._build_ui()
        self._connect_signals()

        # Show onboarding on first launch
        if self._config.first_launch:
            GLib.idle_add(self._show_onboarding)

    # ==================================================================
    # UI Construction
    # ==================================================================

    def _build_ui(self) -> None:
        """Construct the entire widget hierarchy."""
        # Toast overlay (root container for showing toast notifications)
        self._toast_overlay = Adw.ToastOverlay()
        self.set_content(self._toast_overlay)

        # Toolbar view with header bar
        toolbar_view = Adw.ToolbarView()
        self._toast_overlay.set_child(toolbar_view)

        header_bar = Adw.HeaderBar()
        toolbar_view.add_top_bar(header_bar)

        # Scrollable content area
        scrolled = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER,
            vscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
        )
        toolbar_view.set_content(scrolled)

        # Clamp for max content width
        clamp = Adw.Clamp(maximum_size=400, margin_start=16, margin_end=16)
        scrolled.set_child(clamp)

        # Main vertical box
        main_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=24,
            margin_top=24,
            margin_bottom=24,
        )
        clamp.set_child(main_box)

        # Build sections
        main_box.append(self._build_hero_section())
        main_box.append(self._build_toggle_section())
        main_box.append(self._build_status_section())
        main_box.append(self._build_devices_section())
        main_box.append(self._build_settings_section())
        main_box.append(self._build_volume_section())

    # ------------------------------------------------------------------
    # Hero Section
    # ------------------------------------------------------------------

    def _build_hero_section(self) -> Gtk.Widget:
        """Large icon and title at the top."""
        box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=8,
            halign=Gtk.Align.CENTER,
        )

        # Icon — use custom logo if available, fallback to themed icon
        icon_paths = [
            Path(__file__).parent.parent / "resources" / "bluzy.png",
            Path.home() / ".local" / "share" / "bluzy" / "resources" / "bluzy.png",
        ]
        icon_loaded = False
        for icon_path in icon_paths:
            if icon_path.exists():
                picture = Gtk.Picture.new_for_filename(str(icon_path))
                picture.set_size_request(96, 96)
                picture.set_content_fit(Gtk.ContentFit.CONTAIN)
                picture.add_css_class("hero-icon")
                box.append(picture)
                icon_loaded = True
                break

        if not icon_loaded:
            icon = Gtk.Image.new_from_icon_name("bluetooth-active-symbolic")
            icon.set_pixel_size(64)
            icon.add_css_class("hero-icon")
            box.append(icon)

        # Title
        title = Gtk.Label(label="Bluetooth Speaker Mode")
        title.add_css_class("title-1")
        box.append(title)

        # Subtitle
        subtitle = Gtk.Label(
            label="Turn your laptop into a wireless speaker"
        )
        subtitle.add_css_class("dim-label")
        box.append(subtitle)

        return box

    # ------------------------------------------------------------------
    # Toggle Section
    # ------------------------------------------------------------------

    def _build_toggle_section(self) -> Gtk.Widget:
        """Speaker mode on/off toggle."""
        group = Adw.PreferencesGroup()

        self._switch_row = Adw.SwitchRow(
            title="Enable Speaker Mode",
            subtitle="Make this device visible as a Bluetooth speaker",
        )
        self._switch_row.connect("notify::active", self._on_toggle_changed)
        group.add(self._switch_row)

        return group

    # ------------------------------------------------------------------
    # Status Section
    # ------------------------------------------------------------------

    def _build_status_section(self) -> Gtk.Widget:
        """Real-time connection status with animated indicator dot."""
        box = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=10,
            margin_start=4,
        )

        # Animated status dot
        self._status_dot = Gtk.DrawingArea()
        self._status_dot.set_size_request(12, 12)
        self._status_dot.add_css_class("status-dot")
        self._status_dot.add_css_class("status-idle")
        self._status_dot.set_valign(Gtk.Align.CENTER)
        box.append(self._status_dot)

        # Status text
        self._status_label = Gtk.Label(label="Speaker mode is off")
        self._status_label.set_halign(Gtk.Align.START)
        self._status_label.add_css_class("body")
        box.append(self._status_label)

        return box

    # ------------------------------------------------------------------
    # Connected Devices Section
    # ------------------------------------------------------------------

    def _build_devices_section(self) -> Gtk.Widget:
        """List of currently connected devices."""
        self._devices_group = Adw.PreferencesGroup(
            title="Connected Devices",
            description="No devices connected",
        )
        return self._devices_group

    # ------------------------------------------------------------------
    # Settings Section
    # ------------------------------------------------------------------

    def _build_settings_section(self) -> Gtk.Widget:
        """Configuration options: alias, confirmation, autostart."""
        group = Adw.PreferencesGroup(title="Settings")

        # Speaker alias name
        self._alias_entry = Adw.EntryRow(
            title="Speaker Name",
        )
        self._alias_entry.set_text(self._config.speaker_alias)
        self._alias_entry.connect("changed", self._on_alias_changed)
        group.add(self._alias_entry)

        # Require pairing confirmation toggle
        confirm_row = Adw.SwitchRow(
            title="Require Pairing Confirmation",
            subtitle="Ask before accepting new device connections",
        )
        confirm_row.set_active(self._config.require_pairing_confirmation)
        confirm_row.connect(
            "notify::active", self._on_confirm_toggle_changed
        )
        group.add(confirm_row)

        # Autostart toggle
        autostart_row = Adw.SwitchRow(
            title="Launch on Login",
            subtitle="Start Bluzy automatically when you log in",
        )
        autostart_row.set_active(self._config.autostart)
        autostart_row.connect(
            "notify::active", self._on_autostart_toggle_changed
        )
        group.add(autostart_row)

        return group

    # ------------------------------------------------------------------
    # Volume Section
    # ------------------------------------------------------------------

    def _build_volume_section(self) -> Gtk.Widget:
        """Volume control slider for incoming audio."""
        group = Adw.PreferencesGroup(title="Volume")

        box = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=12,
            margin_start=12,
            margin_end=12,
            margin_top=8,
            margin_bottom=8,
        )

        # Volume down icon
        icon_low = Gtk.Image.new_from_icon_name("audio-volume-low-symbolic")
        box.append(icon_low)

        # Slider
        self._volume_scale = Gtk.Scale.new_with_range(
            Gtk.Orientation.HORIZONTAL, 0, 100, 5
        )
        self._volume_scale.set_hexpand(True)
        self._volume_scale.set_draw_value(True)
        self._volume_scale.set_value_pos(Gtk.PositionType.RIGHT)

        # Set current volume
        current_vol = self._audio.get_volume()
        if current_vol is not None:
            self._volume_scale.set_value(current_vol)
        else:
            self._volume_scale.set_value(50)

        self._volume_scale.connect("value-changed", self._on_volume_changed)
        box.append(self._volume_scale)

        # Volume up icon
        icon_high = Gtk.Image.new_from_icon_name(
            "audio-volume-high-symbolic"
        )
        box.append(icon_high)

        row = Adw.ActionRow()
        row.set_child(box)
        group.add(row)

        return group

    # ==================================================================
    # Signal Connections
    # ==================================================================

    def _connect_signals(self) -> None:
        """Connect to BluetoothManager and AudioManager signals."""
        self._bt.connect("state-changed", self._on_state_changed)
        self._bt.connect("device-connected", self._on_device_connected)
        self._bt.connect("device-disconnected", self._on_device_disconnected)
        self._bt.connect("streaming-started", self._on_streaming_started)
        self._bt.connect("streaming-stopped", self._on_streaming_stopped)
        self._bt.connect("error", self._on_error)
        self._bt.connect("pairing-requested", self._on_pairing_requested)
        self._audio.connect("error", self._on_error)

    # ==================================================================
    # UI Event Handlers
    # ==================================================================

    def _on_toggle_changed(
        self, switch_row: Adw.SwitchRow, _pspec
    ) -> None:
        """Handle the speaker mode toggle switch."""
        if self._suppress_switch_signal:
            return

        enabled = switch_row.get_active()

        if enabled:
            success = self._bt.enable()
            if success:
                self._audio.start_monitoring()
                self._show_toast("Speaker mode enabled")
            else:
                # Revert the switch
                self._suppress_switch_signal = True
                switch_row.set_active(False)
                self._suppress_switch_signal = False
        else:
            self._bt.disable()
            self._audio.stop_monitoring()
            self._audio.reset_profile()
            self._show_toast("Speaker mode disabled")

    def _on_alias_changed(self, entry: Adw.EntryRow) -> None:
        """Handle speaker alias name changes."""
        new_alias = entry.get_text().strip()
        if new_alias and new_alias != self._config.speaker_alias:
            self._config.speaker_alias = new_alias
            logger.info("Speaker alias updated to: %s", new_alias)

    def _on_confirm_toggle_changed(
        self, row: Adw.SwitchRow, _pspec
    ) -> None:
        """Handle pairing confirmation toggle."""
        self._config.require_pairing_confirmation = row.get_active()

    def _on_autostart_toggle_changed(
        self, row: Adw.SwitchRow, _pspec
    ) -> None:
        """Handle autostart toggle."""
        enabled = row.get_active()
        self._config.autostart = enabled
        self._manage_autostart(enabled)

    def _on_volume_changed(self, scale: Gtk.Scale) -> None:
        """Handle volume slider changes."""
        volume = int(scale.get_value())
        self._audio.set_volume(volume)

    # ==================================================================
    # BluetoothManager Signal Handlers
    # ==================================================================

    def _on_state_changed(self, _manager, state_name: str) -> None:
        """Update UI based on speaker mode state."""
        try:
            state = SpeakerState[state_name]
        except KeyError:
            return

        status_messages = {
            SpeakerState.OFF: "Speaker mode is off",
            SpeakerState.WAITING: "Waiting for connection…",
            SpeakerState.CONNECTED: "Device connected",
            SpeakerState.STREAMING: "Streaming audio 🎵",
            SpeakerState.ERROR: "An error occurred",
        }

        self._status_label.set_text(
            status_messages.get(state, "Unknown state")
        )

        # Update status dot CSS class
        self._status_dot.remove_css_class("status-idle")
        self._status_dot.remove_css_class("status-waiting")
        self._status_dot.remove_css_class("status-connected")
        self._status_dot.remove_css_class("status-streaming")

        dot_classes = {
            SpeakerState.OFF: "status-idle",
            SpeakerState.WAITING: "status-waiting",
            SpeakerState.CONNECTED: "status-connected",
            SpeakerState.STREAMING: "status-streaming",
        }
        self._status_dot.add_css_class(
            dot_classes.get(state, "status-idle")
        )

        # Sync toggle switch state
        if state == SpeakerState.OFF:
            self._suppress_switch_signal = True
            self._switch_row.set_active(False)
            self._suppress_switch_signal = False

    def _on_device_connected(
        self, _manager, name: str, address: str
    ) -> None:
        """Add a connected device to the UI list."""
        self._show_toast(f"Connected: {name}")
        self._send_notification("Device Connected", f"{name} ({address})")

        # Add device row
        if address not in self._device_rows:
            row = Adw.ActionRow(
                title=name,
                subtitle=address,
            )
            row.add_prefix(
                Gtk.Image.new_from_icon_name("phone-symbolic")
            )

            # Disconnect button
            disconnect_btn = Gtk.Button(
                icon_name="user-trash-symbolic",
                valign=Gtk.Align.CENTER,
                tooltip_text="Disconnect",
            )
            disconnect_btn.add_css_class("flat")
            disconnect_btn.connect(
                "clicked",
                lambda _btn, addr=address: self._bt.disconnect_device(addr),
            )
            row.add_suffix(disconnect_btn)

            # Auto-enable toggle
            auto_switch = Gtk.Switch(valign=Gtk.Align.CENTER)
            device_cfg = self._config.get_device_config(address)
            auto_switch.set_active(
                device_cfg.get("auto_enable", False)
                if device_cfg
                else False
            )
            auto_switch.set_tooltip_text(
                "Auto-enable speaker mode for this device"
            )
            auto_switch.connect(
                "notify::active",
                lambda sw, _p, n=name, a=address: (
                    self._config.set_device_auto_enable(
                        a, n, sw.get_active()
                    )
                ),
            )
            row.add_suffix(auto_switch)

            self._devices_group.add(row)
            self._device_rows[address] = row

        # Update description
        count = len(self._device_rows)
        self._devices_group.set_description(
            f"{count} device{'s' if count != 1 else ''} connected"
        )

        # Set the audio profile to A2DP sink for the new device
        self._audio.set_a2dp_sink()

    def _on_device_disconnected(self, _manager, address: str) -> None:
        """Remove a disconnected device from the UI list."""
        row = self._device_rows.pop(address, None)
        if row:
            self._devices_group.remove(row)

        count = len(self._device_rows)
        if count == 0:
            self._devices_group.set_description("No devices connected")
        else:
            self._devices_group.set_description(
                f"{count} device{'s' if count != 1 else ''} connected"
            )

        self._show_toast("Device disconnected")

    def _on_streaming_started(self, _manager) -> None:
        """UI feedback when audio streaming begins."""
        pass  # State change handles the dot/label update

    def _on_streaming_stopped(self, _manager) -> None:
        """UI feedback when audio streaming stops."""
        pass

    def _on_error(self, _manager, message: str) -> None:
        """Display error messages as toast notifications."""
        self._show_toast(message, is_error=True)

    def _on_pairing_requested(
        self, _manager, device_path: str, device_name: str, passkey: int
    ) -> None:
        """Show a dialog asking the user to confirm pairing."""
        dialog = Adw.AlertDialog(
            heading="Pairing Request",
            body=(
                f"Device '{device_name}' wants to connect.\n"
                f"Confirm passkey: {passkey:06d}"
            ),
        )
        dialog.add_response("reject", "Reject")
        dialog.add_response("accept", "Accept")
        dialog.set_response_appearance(
            "reject", Adw.ResponseAppearance.DESTRUCTIVE
        )
        dialog.set_response_appearance(
            "accept", Adw.ResponseAppearance.SUGGESTED
        )
        dialog.set_default_response("accept")

        dialog.connect(
            "response",
            self._on_pairing_dialog_response,
        )
        dialog.present(self)

    def _on_pairing_dialog_response(
        self, dialog: Adw.AlertDialog, response: str
    ) -> None:
        """Handle the user's pairing confirmation response."""
        accepted = response == "accept"
        self._bt.respond_to_pairing(accepted)
        if accepted:
            self._show_toast("Pairing accepted")
        else:
            self._show_toast("Pairing rejected")

    # ==================================================================
    # Helpers
    # ==================================================================

    def _show_toast(
        self, message: str, *, is_error: bool = False
    ) -> None:
        """Display a toast notification in the UI."""
        toast = Adw.Toast.new(message)
        toast.set_timeout(4 if is_error else 2)
        self._toast_overlay.add_toast(toast)

    def _send_notification(self, title: str, body: str) -> None:
        """Send a system notification via Gio."""
        notification = Gio.Notification.new(title)
        notification.set_body(body)
        notification.set_icon(
            Gio.ThemedIcon.new("bluetooth-active-symbolic")
        )
        self.get_application().send_notification(None, notification)

    def _show_onboarding(self) -> bool:
        """Show a first-launch onboarding dialog."""
        dialog = Adw.AlertDialog(
            heading="Welcome to Bluzy! 🔊",
            body=(
                "Bluzy turns your laptop into a Bluetooth speaker.\n\n"
                "1. Enable Speaker Mode\n"
                "2. Connect your phone via Bluetooth\n"
                "3. Play audio — it comes through your laptop!\n\n"
                "Your laptop will appear as a speaker in your "
                "phone's Bluetooth settings."
            ),
        )
        dialog.add_response("ok", "Get Started")
        dialog.set_response_appearance(
            "ok", Adw.ResponseAppearance.SUGGESTED
        )
        dialog.connect(
            "response",
            lambda _d, _r: None,
        )
        dialog.present(self)

        self._config.first_launch = False
        return False  # Remove from GLib idle

    def _manage_autostart(self, enabled: bool) -> None:
        """Add or remove the .desktop autostart entry."""
        autostart_dir = Path.home() / ".config" / "autostart"
        autostart_file = autostart_dir / "com.github.izzathnisfer.bluzy.desktop"

        if enabled:
            autostart_dir.mkdir(parents=True, exist_ok=True)
            desktop_content = (
                "[Desktop Entry]\n"
                "Name=Bluzy\n"
                "Comment=Turn your laptop into a Bluetooth speaker\n"
                "Exec=python3 {install_dir}/main.py --minimized\n"
                "Icon=bluzy\n"
                "Type=Application\n"
                "X-GNOME-Autostart-enabled=true\n"
                "Hidden=false\n"
            ).format(
                install_dir=os.path.expanduser("~/.local/share/bluzy")
            )
            autostart_file.write_text(desktop_content)
            logger.info("Autostart enabled.")
        else:
            if autostart_file.exists():
                autostart_file.unlink()
                logger.info("Autostart disabled.")
