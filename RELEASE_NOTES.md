# Bluzy v2.0.1

Choose where your phone's Bluetooth audio plays independently of your desktop output and other applications, like VLC's Audio Device menu.

- Select built-in audio, USB headphones, HDMI, Bluetooth audio, or another independently exposed output.
- Switch Bluzy's playback while other applications continue using their chosen outputs.
- Adjust only Bluetooth playback volume with **Bluzy Volume**.
- Remember the output and stream volume across restarts and phone reconnects.
- Temporarily use the desktop default while the chosen device is disconnected, then restore the choice when it returns.

The selector follows VLC's PulseAudio model: it moves incoming Bluetooth playback streams. It leaves the desktop default, hardware ports, card profiles, shared device volume, and unrelated application streams unchanged. Shared ports on one output are not separate per-app destinations.

## Install or upgrade

Download `bluzy_2.0.1_all.deb`, then run:

```bash
sudo apt install ./bluzy_2.0.1_all.deb
```

## Validation

- 29 backend and GTK tests passed, including 256 output-routing combinations.
- Silent live tests passed in both directions on built-in audio and a USB headset, preserving other audio settings and application streams.
- Reconnect, hotplug fallback, saved preferences, and independent stream-volume checks passed.
- Real phone playback confirmed working on the installed application.
- Package/version and AppStream checks passed.

This release includes the correction to the locally tested 2.0.0 build: shared port switching and device-volume changes were replaced with independent stream routing and volume.
