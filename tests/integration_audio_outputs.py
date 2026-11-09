#!/usr/bin/env python3
"""Exercise real PipeWire/PulseAudio routing with temporary silent devices.

Requires a running user audio server and pw-cat. Never changes the system
default, uses only temporary null sinks, and removes all test objects on exit.
"""

import json
from pathlib import Path
import subprocess
import sys
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bluzy.audio_outputs import OutputTarget, PulseAudioOutputs


def pactl(*args):
    return subprocess.check_output(["pactl", *args], text=True, timeout=5).strip()


def listing(kind):
    return json.loads(pactl("--format=json", "list", kind))


def wait_for(predicate):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.05)
    raise AssertionError("Audio server did not reach the expected state")


def main():
    # Routing the laptop owner's real phone stream is reserved for their
    # listening test; this integration test should touch only its own streams.
    if any(str(s.get("properties", {}).get("node.name", "")).startswith("bluez_input.") for s in listing("sink-inputs")):
        raise RuntimeError("Pause/disconnect Bluetooth playback before running this isolated integration test.")
    prefix = "bluzy_test_" + uuid.uuid4().hex[:8]
    names = [prefix + "_a", prefix + "_b"]
    modules = []
    players = []
    default = pactl("get-default-sink")
    backend = PulseAudioOutputs()

    def play(name, target):
        player = subprocess.Popen([
            "pw-cat", "--playback", "--raw", "--rate", "48000",
            "--channels", "2", "--target", target,
            "--properties", json.dumps({"node.name": name}), "/dev/zero",
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        players.append(player)
        return wait_for(lambda: next((s for s in listing("sink-inputs") if s.get("properties", {}).get("node.name") == name), None))

    try:
        for name in names:
            modules.append(pactl("load-module", "module-null-sink", f"sink_name={name}", f"sink_properties=device.description={name}"))
        wait_for(lambda: all(any(s["name"] == name for s in listing("sinks")) for name in names))
        a = next(s for s in listing("sinks") if s["name"] == names[0])
        b = next(s for s in listing("sinks") if s["name"] == names[1])
        phone = play("bluez_input." + prefix, names[0])
        other = play(prefix + ".unrelated", names[0])
        backend.select(OutputTarget(names[1]), route=True)
        wait_for(lambda: any(s["index"] == phone["index"] and s["sink"] == b["index"] for s in listing("sink-inputs")))
        assert any(s["index"] == other["index"] and s["sink"] == a["index"] for s in listing("sink-inputs"))
        print("PASS: live Bluetooth-like stream moved; unrelated stream stayed on its original output")
        backend.set_volume(names[1], 37)
        assert backend.refresh().volume == 37
        print("PASS: volume targets the selected virtual output")
        players[0].terminate()
        players[0].wait(timeout=5)
        phone = play("bluez_input." + prefix + ".reconnected", names[0])
        backend.refresh(route=True)
        wait_for(lambda: any(s["index"] == phone["index"] and s["sink"] == b["index"] for s in listing("sink-inputs")))
        print("PASS: reconnected phone stream follows saved output")
        for player in players:
            player.terminate()
            player.wait(timeout=5)
        players.clear()
        pactl("unload-module", modules.pop())
        wait_for(lambda: not any(s["name"] == names[1] for s in listing("sinks")))
        state = backend.refresh()
        assert state.missing and state.effective_sink == default
        assert state.preferred == OutputTarget(names[1])
        modules.append(pactl("load-module", "module-null-sink", f"sink_name={names[1]}"))
        wait_for(lambda: any(s["name"] == names[1] for s in listing("sinks")))
        assert not backend.refresh().missing
        assert pactl("get-default-sink") == default
        print("PASS: unplug/replug fallback preserves preference and desktop default")
    finally:
        for player in players:
            player.terminate()
            player.wait(timeout=5)
        for module in reversed(modules):
            subprocess.run(["pactl", "unload-module", module], check=False, capture_output=True, timeout=5)
    print("All live audio integration checks passed; temporary devices removed.")


if __name__ == "__main__":
    main()
