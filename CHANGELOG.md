# Changelog

## 2.0.0 — 2026-11-09

- Choose the playback output directly in Bluzy: laptop speakers, wired headphones, USB audio, HDMI, and other outputs exposed by PipeWire/PulseAudio.
- Select speaker and headphone ports separately, even when they share a sound card.
- Switch incoming Bluetooth playback while music is playing, without changing the desktop default or moving unrelated apps' streams.
- Save the chosen output across restarts and phone reconnects. Fall back to the system default while it is unplugged, then restore it on reconnect.
- Follow the desktop output with the System Default option.
- Adjust volume on the selected output. Output discovery, routing, and volume changes run in the background to keep the interface responsive.
- Add automated routing and GTK tests, a live audio integration check, and package CI.

Port selection and device volume are shared with other apps using that physical device. The selector lists active outputs; enable inactive sound-card profiles in desktop Sound settings first.

## 1.0.0 — 2026-03-31

- Initial Bluetooth Speaker Mode release with connection status, tray controls, pairing confirmation, trusted-device memory, and volume control.
