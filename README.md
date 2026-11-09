# 🔊 Bluzy — Bluetooth Speaker Mode for Ubuntu

<p align="center">
  <img src="resources/bluzy.png" alt="Bluzy" width="128" height="128">
</p>

<p align="center">
  <strong>Turn your Ubuntu laptop into a wireless Bluetooth speaker.</strong>
</p>

<p align="center">
  <a href="#installation">Install</a> •
  <a href="#usage">Usage</a> •
  <a href="#troubleshooting">Troubleshoot</a> •
  <a href="#contributing">Contribute</a>
</p>

---

## What is Bluzy?

Bluzy is a native GNOME desktop application that turns your Ubuntu laptop into a **Bluetooth A2DP audio receiver** (A2DP Sink). Connect your phone via Bluetooth and play audio through your laptop's speakers — wirelessly.

### Features

- 🎛️ **One-toggle Speaker Mode** — Enable/disable with a single switch
- 📱 **Real-time Status** — See connected device name, streaming state, and animated indicators
- 🔔 **System Tray** — Quick access from the system tray with status-aware icon
- 🔒 **Pairing Confirmation** — Optional security prompt for new device connections
- 💾 **Auto-Connect Memory** — Remember trusted devices and auto-enable per device
- 🔊 **Volume Control** — Adjust incoming audio volume
- 🎧 **Audio Output Selector** — Route Bluzy's audio independently to speakers, headphones, USB, HDMI, or another output
- 🔄 **Remembered Output** — Restore your choice on restart and reconnect; fall back to the system default while it is unplugged
- 🎨 **Native GNOME Look** — Built with GTK4 + Libadwaita, follows HIG
- 🌙 **Dark/Light Mode** — Automatic via Adwaita style manager

---

## Requirements

| Requirement | Version |
|---|---|
| **Ubuntu** | 24.04+ or 25.04 |
| **Python** | 3.10+ |
| **PipeWire** | Default on Ubuntu 24.04+ |
| **BlueZ** | 5.x |
| **Bluetooth hardware** | Built-in or USB adapter |

---

## Installation

### 📦 Download & Install (Recommended)

Download the latest `.deb` package from [GitHub Releases](https://github.com/izzathnisfer/Bluzy/releases):

```bash
# Download the latest release
wget https://github.com/izzathnisfer/Bluzy/releases/download/v2.0.1/bluzy_2.0.1_all.deb

# Install (auto-resolves all dependencies)
sudo apt install ./bluzy_2.0.1_all.deb
```

Or simply **double-click** the `.deb` file in your file manager to install via GNOME Software.

After installation:
- 🔍 Search **"Bluzy"** in your app grid
- 🖥️ Or run `bluzy` from any terminal

To uninstall:

```bash
sudo apt remove bluzy
```

---

### 🔧 Install from Source

```bash
git clone https://github.com/izzathnisfer/Bluzy.git
cd Bluzy
bash install.sh
```

The installer will:
1. Install all system dependencies via `apt`
2. Start and enable the Bluetooth service
3. Add your user to the `bluetooth` group (if needed)
4. Deploy app files to `~/.local/share/bluzy/`
5. Install the `.desktop` file and icon for your app drawer

To uninstall a source installation:

```bash
bash uninstall.sh
```

### Manual Dependencies

If you prefer to install dependencies manually:

```bash
sudo apt install -y \
    python3-gi \
    python3-gi-cairo \
    gir1.2-gtk-4.0 \
    gir1.2-adw-1 \
    gir1.2-appindicator3-0.1 \
    python3-dbus \
    bluez \
    pipewire \
    wireplumber \
    pulseaudio-utils
```

---

## Usage

### From the App Drawer

After installation, search **"Bluzy"** in your Ubuntu app drawer and click to launch.

### From the Terminal

```bash
python3 ~/.local/share/bluzy/main.py
```

Or during development:

```bash
python3 bluzy/main.py --debug
```

### Command-Line Options

| Flag | Description |
|---|---|
| `--minimized` | Start minimized in the system tray |
| `--debug` | Enable debug logging |
| `--version` | Print version and exit |

### How to Use

1. **Launch Bluzy**
2. **Enable Speaker Mode** — flip the toggle switch
3. **Connect your phone** — open Bluetooth settings on your phone, you'll see "Bluzy Speaker" (or your custom name)
4. **Play audio** — music, podcasts, or any audio will play through your laptop speakers!
5. **Choose where to listen** — use **Audio Output → Playback Device** to select an output for Bluzy's incoming phone audio, independently of your desktop default and other apps. You can change it while music is playing, as in VLC's Audio Device menu.
6. **Disable** — flip the toggle off to disconnect and stop being discoverable

### Audio output selection (v2.0)

**System Default** follows your desktop's current default output. A specific choice is saved and reapplied to Bluetooth playback, including new streams when your phone reconnects. Devices refresh automatically. If your saved device is disconnected, Bluzy temporarily uses the system default and restores your choice when the device returns.

The selector follows the same approach as [VLC's PulseAudio output](https://github.com/videolan/vlc/blob/3.0.x/modules/audio_output/pulse.c): it lists independently routable output devices and moves only incoming Bluetooth playback. Other apps can use any other output, or share the same output. **Bluzy Volume** adjusts only the phone's stream.

Bluzy leaves the desktop default, hardware ports, card profiles, device volume, and unrelated application streams unchanged. A speaker/headphone switch shared by one physical output is not a separate per-app destination; simultaneous playback needs independently exposed outputs, such as built-in audio and a USB headset. The device list uses the outputs exposed by your audio service, like VLC's PulseAudio backend.

---

## Bluetooth Configuration (Advanced)

### A2DP Sink Role

Modern BlueZ (5.x) with PipeWire handles A2DP profiles automatically. No changes to `/etc/bluetooth/main.conf` are typically needed.

If you experience issues, ensure the following in `/etc/bluetooth/main.conf`:

```ini
[General]
# Allow other devices to discover this computer
DiscoverableTimeout = 0

[Policy]
# Auto-enable Bluetooth adapter on boot
AutoEnable=true
```

After editing, restart the Bluetooth service:

```bash
sudo systemctl restart bluetooth
```

---

## Troubleshooting

### Bluetooth adapter not found

```bash
# Check if adapter is blocked
rfkill list bluetooth

# Unblock if blocked
sudo rfkill unblock bluetooth

# Check if adapter is detected
hciconfig -a
```

### Bluetooth service not running

```bash
sudo systemctl start bluetooth
sudo systemctl enable bluetooth
systemctl status bluetooth
```

### User not in bluetooth group

```bash
sudo usermod -aG bluetooth $USER
# Then log out and log back in
```

### Phone can't see "Bluzy Speaker"

1. Make sure Speaker Mode is **enabled** in the app
2. Check that your adapter is discoverable:
   ```bash
   bluetoothctl show | grep Discoverable
   ```
3. Try restarting Bluetooth on both devices

### Audio not playing through laptop speakers

1. Check PipeWire/PulseAudio is running:
   ```bash
   pactl info
   ```
2. Check available Bluetooth cards:
   ```bash
   pactl list cards short | grep bluez
   ```
3. Check the active profile:
   ```bash
   pactl list cards | grep -A 20 bluez
   ```

### AppIndicator (tray icon) not showing

On GNOME, you need the **AppIndicator extension**:

```bash
sudo apt install gnome-shell-extension-appindicator
```

Then enable it in GNOME Extensions or restart your session.

---

## Project Structure

```
bluzy/
├── bluzy/                           # Python package
│   ├── __init__.py                  # Package metadata & version
│   ├── main.py                      # Entry point
│   ├── app.py                       # Adw.Application lifecycle
│   ├── window.py                    # Main window UI
│   ├── tray.py                      # System tray (GTK3 subprocess)
│   ├── tray_dbus.py                 # D-Bus IPC for app↔tray
│   ├── bluetooth_manager.py         # BlueZ D-Bus integration
│   ├── bluetooth_agent.py           # Auto-accept pairing agent
│   ├── audio_manager.py             # Bluetooth playback lifecycle
│   ├── audio_outputs.py             # Independent device routing and stream volume
│   ├── config_manager.py            # User preferences
│   └── utils.py                     # System checks & helpers
├── resources/
│   ├── bluzy.png                    # App icon
│   ├── bluzy-tray-*.svg             # Tray state icons
│   ├── style.css                    # Custom GTK4 CSS
│   └── bluzy.gresource.xml          # GResource bundle
├── data/
│   ├── com.github.izzathnisfer.bluzy.desktop
│   └── com.github.izzathnisfer.bluzy.metainfo.xml
├── packaging/
│   └── build-deb.sh                 # .deb package builder
├── dist/                            # Built packages (gitignored)
├── install.sh                       # One-command source installer
├── uninstall.sh                     # Source install uninstaller
├── requirements.txt                 # Python dependencies
└── README.md                        # This file
```

---

## Architecture

Bluzy uses a **two-process architecture** to bridge the GTK3/GTK4 incompatibility with AppIndicator3:

```
┌─────────────────────────┐     D-Bus (session bus)     ┌──────────────────┐
│   Main App (GTK4)       │◄──────────────────────────►│  Tray (GTK3)     │
│                         │  com.github.bluzy.App       │                  │
│  • Adw.ApplicationWindow│                             │  • AppIndicator3 │
│  • BluetoothManager     │  Methods:                   │  • Right-click   │
│  • AudioManager         │    Toggle(), Show(), Quit() │    menu          │
│  • ConfigManager        │  Signals:                   │  • Icon changes  │
│  • BluzyDBusService     │    StateChanged(...)        │                  │
└─────────────────────────┘                             └──────────────────┘
         │
         │ D-Bus (system bus)
         │ org.bluez
         ▼
┌─────────────────────────┐
│   BlueZ (bluetoothd)    │
│                         │
│  • Adapter1             │
│  • Device1              │
│  • Agent1               │
│  • MediaTransport1      │
└─────────────────────────┘
```

---

## Contributing

Contributions are welcome! Here's how to get started:

### Development Setup

```bash
git clone https://github.com/izzathnisfer/Bluzy.git
cd Bluzy

# Install dependencies
sudo apt install -y python3-gi python3-gi-cairo gir1.2-gtk-4.0 \
    gir1.2-adw-1 gir1.2-appindicator3-0.1 python3-dbus bluez \
    pipewire wireplumber pulseaudio-utils

# Run in debug mode
python3 bluzy/main.py --debug
```

### Code Style

- Follow PEP 8
- Use type hints for all function signatures
- Add docstrings to all public classes and methods
- Keep modules focused and loosely coupled

### Tests

```bash
python3 -m unittest discover -s tests -v
bash packaging/build-deb.sh
```

Output tests cover independent device discovery, 256 combinations of default/source/destination/other-app outputs, Bluetooth-only routing and volume, saved preferences, hotplug fallback, audio-service failures, and non-blocking GTK updates. To exercise routing against a running PipeWire/PulseAudio server using temporary silent virtual devices:

```bash
python3 tests/integration_audio_outputs.py
```

To repeat both-direction isolation checks on your real outputs with silent test streams:

```bash
python3 tests/integration_audio_outputs.py --hardware
```

### Submitting Changes

1. Fork the repository
2. Create a feature branch: `git checkout -b feature/my-feature`
3. Commit with clear messages: `git commit -m "Add: volume slider"`
4. Push and open a Pull Request

---

## License

MIT License — see [LICENSE](LICENSE) for details.

```
MIT License

Copyright (c) 2026 Bluzy Contributors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```
