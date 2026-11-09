# Bluzy — Audio Manager
# Copyright (c) 2026 Bluzy Contributors
# SPDX-License-Identifier: MIT

"""
PipeWire / PulseAudio audio profile management.

Uses ``pactl`` (works with both PipeWire-pulse and PulseAudio) to:

- List Bluetooth audio cards
- Auto-discover and switch card profiles for A2DP sink mode
- Reset profiles when speaker mode is disabled
- Control incoming audio volume

All operations are non-blocking — retries use GLib timeouts instead of
time.sleep() to keep the GTK main loop responsive.
"""

import logging
import re
import subprocess
from typing import Optional

from gi.repository import GLib, GObject

from bluzy.audio_outputs import AudioOutputs
from bluzy.config_manager import ConfigManager

logger = logging.getLogger(__name__)

# Regex to extract bluez card names from pactl output
_BLUEZ_CARD_RE = re.compile(r"(bluez_card\.\S+)")

# Profile names to try, in preference order.
# Different PipeWire/PulseAudio versions use different names.
A2DP_SINK_PROFILES = [
    "a2dp-sink",
    "a2dp_sink",
    "a2dp-sink-sbc",
    "a2dp-sink-aac",
    "a2dp_sink_sbc",
]

PROFILE_OFF = "off"

# Retry configuration
MAX_RETRIES = 3
RETRY_DELAY_MS = 2000  # milliseconds (non-blocking via GLib.timeout_add)


class AudioManager(GObject.Object):
    """
    Manages audio profiles for Bluetooth cards.

    All retry logic runs asynchronously via GLib timeouts — the
    main loop is NEVER blocked.

    Emits GObject signals:

    - ``profile-set(card_name: str, profile: str)`` — profile switch succeeded
    - ``volume-changed(volume_percent: int)`` — volume was adjusted
    - ``error(message: str)`` — an operation failed
    """

    __gsignals__ = {
        "profile-set": (
            GObject.SignalFlags.RUN_FIRST,
            None,
            (str, str),  # card_name, profile
        ),
        "volume-changed": (
            GObject.SignalFlags.RUN_FIRST,
            None,
            (int,),  # volume_percent
        ),
        "error": (
            GObject.SignalFlags.RUN_FIRST,
            None,
            (str,),
        ),
    }

    def __init__(self, config: ConfigManager) -> None:
        super().__init__()
        self.outputs = AudioOutputs(config)
        self._active_cards: set[str] = set()
        self._attempted_cards: set[str] = set()  # Tracks all seen cards
        self._monitor_source_id: Optional[int] = None

    # ------------------------------------------------------------------
    # Card discovery
    # ------------------------------------------------------------------

    def get_bluetooth_cards(self) -> list[str]:
        """
        List all Bluetooth audio cards known to PipeWire/PulseAudio.

        Returns:
            List of card names (e.g. ``['bluez_card.AA_BB_CC_DD_EE_FF']``).
        """
        try:
            result = subprocess.run(
                ["pactl", "list", "cards", "short"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode != 0:
                logger.warning("pactl list cards failed: %s", result.stderr)
                return []

            cards = _BLUEZ_CARD_RE.findall(result.stdout)
            return cards

        except (subprocess.SubprocessError, FileNotFoundError) as exc:
            logger.error("Failed to list audio cards: %s", exc)
            return []

    def get_card_profiles(self, card_name: str) -> list[str]:
        """
        Discover available profiles for a specific card.

        Returns:
            List of profile name strings.
        """
        try:
            result = subprocess.run(
                ["pactl", "list", "cards"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode != 0:
                return []

            # Find the section for our card and extract profile names
            profiles = []
            in_card = False
            in_profiles = False

            for line in result.stdout.splitlines():
                if card_name in line:
                    in_card = True
                    continue
                if in_card and "Profiles:" in line:
                    in_profiles = True
                    continue
                if in_card and in_profiles:
                    # Profile lines look like:
                    #   "a2dp-sink: ...(available)" or similar
                    stripped = line.strip()
                    if not stripped or stripped.startswith("Active Profile"):
                        in_profiles = False
                        in_card = False
                        continue
                    # Extract profile name (before the colon)
                    if ":" in stripped:
                        profile_name = stripped.split(":")[0].strip()
                        profiles.append(profile_name)

            logger.debug(
                "Available profiles for %s: %s", card_name, profiles
            )
            return profiles

        except (subprocess.SubprocessError, FileNotFoundError) as exc:
            logger.error("Failed to get profiles for %s: %s", card_name, exc)
            return []

    # ------------------------------------------------------------------
    # Profile switching (non-blocking)
    # ------------------------------------------------------------------

    def set_a2dp_sink(self, card_name: Optional[str] = None) -> None:
        """
        Switch a Bluetooth card to an A2DP Sink profile (non-blocking).

        If ``card_name`` is None, attempts to switch the first
        available Bluetooth card. Uses GLib timeouts for retries.
        """
        if card_name is None:
            cards = self.get_bluetooth_cards()
            if not cards:
                logger.warning("No Bluetooth cards available for A2DP sink.")
                return
            card_name = cards[0]

        self._attempt_profile_switch(card_name, attempt=1)

    def reset_profile(self, card_name: Optional[str] = None) -> None:
        """
        Reset a Bluetooth card profile to 'off'.

        Args:
            card_name: The pactl card name. If None, resets all known cards.
        """
        if card_name is None:
            for card in list(self._active_cards):
                self._set_profile(card, PROFILE_OFF)
            self._active_cards.clear()
            self._attempted_cards.clear()
            return

        self._set_profile(card_name, PROFILE_OFF)
        self._active_cards.discard(card_name)

    # ------------------------------------------------------------------
    # Volume control
    # ------------------------------------------------------------------

    def get_volume(self) -> Optional[int]:
        """Return the selected output's most recently discovered volume."""
        return self.outputs.state.volume

    def set_volume(self, percent: int) -> bool:
        """Queue a volume change for the selected output without blocking GTK."""
        if self.outputs.state.effective_sink is None:
            return False
        self.outputs.set_volume(percent)
        return True

    # ------------------------------------------------------------------
    # Monitoring
    # ------------------------------------------------------------------

    def start_monitoring(self) -> None:
        """
        Start polling for new Bluetooth cards at regular intervals.

        When speaker mode is enabled, new cards may appear as devices
        connect. This poller detects them and switches their profile.
        """
        self.outputs.routing_enabled = True
        self.outputs.refresh()
        if self._monitor_source_id is not None:
            return

        self._attempted_cards.clear()
        self._monitor_source_id = GLib.timeout_add_seconds(
            3, self._monitor_tick
        )
        logger.debug("Audio card monitoring started.")

    def stop_monitoring(self) -> None:
        """Stop the card monitoring poller."""
        self.outputs.routing_enabled = False
        if self._monitor_source_id is not None:
            GLib.source_remove(self._monitor_source_id)
            self._monitor_source_id = None
            self._attempted_cards.clear()
            logger.debug("Audio card monitoring stopped.")

    def _monitor_tick(self) -> bool:
        """Check for new Bluetooth cards and set them to A2DP sink."""
        cards = self.get_bluetooth_cards()
        for card in cards:
            # Only attempt cards we haven't tried yet
            if card not in self._attempted_cards:
                logger.info(
                    "New Bluetooth card detected: %s — setting A2DP sink",
                    card,
                )
                self._attempted_cards.add(card)
                self._attempt_profile_switch(card, attempt=1)

        # Clean up attempted cards that no longer exist
        existing = set(cards)
        disappeared = self._attempted_cards - existing
        if disappeared:
            self._attempted_cards -= disappeared
            self._active_cards -= disappeared

        return True  # Keep the timer alive

    # ------------------------------------------------------------------
    # Internal (non-blocking)
    # ------------------------------------------------------------------

    def _attempt_profile_switch(
        self, card_name: str, attempt: int
    ) -> None:
        """
        Try to set an A2DP sink profile. On failure, schedule a retry
        via GLib.timeout_add (non-blocking).
        """
        # First, try the known profile names
        for profile in A2DP_SINK_PROFILES:
            if self._set_profile(card_name, profile):
                self._active_cards.add(card_name)
                return

        # If all hardcoded names fail, auto-discover profiles
        available = self.get_card_profiles(card_name)
        for profile in available:
            # Look for anything that looks like an a2dp sink
            lower = profile.lower()
            if "a2dp" in lower and ("sink" in lower or "audio" in lower):
                if self._set_profile(card_name, profile):
                    self._active_cards.add(card_name)
                    return

        # If nothing worked, retry later (non-blocking)
        if attempt < MAX_RETRIES:
            logger.info(
                "Retry %d/%d for %s in %dms (non-blocking)…",
                attempt,
                MAX_RETRIES,
                card_name,
                RETRY_DELAY_MS,
            )
            GLib.timeout_add(
                RETRY_DELAY_MS,
                self._retry_profile_switch,
                card_name,
                attempt + 1,
            )
        else:
            # Give up — PipeWire may handle it automatically
            logger.warning(
                "Could not set A2DP sink profile on '%s' after %d attempts. "
                "PipeWire may handle this automatically.",
                card_name,
                MAX_RETRIES,
            )

    def _retry_profile_switch(
        self, card_name: str, attempt: int
    ) -> bool:
        """GLib timeout callback for retrying profile switch."""
        self._attempt_profile_switch(card_name, attempt)
        return False  # Remove this timeout (one-shot)

    def _set_profile(self, card_name: str, profile: str) -> bool:
        """Execute a single pactl set-card-profile command."""
        try:
            result = subprocess.run(
                ["pactl", "set-card-profile", card_name, profile],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                logger.info(
                    "Set profile '%s' on card '%s'.", profile, card_name
                )
                self.emit("profile-set", card_name, profile)
                return True

            # Don't log at WARNING for expected "No such entity" during probing
            stderr = result.stderr.strip()
            if "No such entity" in stderr:
                logger.debug(
                    "Profile '%s' not available on %s", profile, card_name
                )
            else:
                logger.warning(
                    "pactl set-card-profile failed for %s/%s: %s",
                    card_name,
                    profile,
                    stderr,
                )
            return False

        except (subprocess.SubprocessError, FileNotFoundError) as exc:
            logger.error(
                "Failed to set profile on %s: %s", card_name, exc
            )
            return False

    def cleanup(self) -> None:
        """Stop monitoring and reset all profiles."""
        self.outputs.cleanup()
        self.stop_monitoring()
        self.reset_profile()
        self._active_cards.clear()
        self._attempted_cards.clear()
        logger.info("AudioManager cleaned up.")
