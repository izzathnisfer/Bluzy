#!/usr/bin/env python3
"""Check isolated stream routing on virtual or real outputs, without sound.

Default: temporary virtual outputs, reconnect and unplug/replug checks.
--hardware: all currently exposed real outputs, including both directions.
Only this test's silent streams may move. Existing audio, the desktop default,
physical ports, mute state and device volume must remain unchanged.
"""

import argparse
import copy
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


def shared_state(sinks):
    return {
        sink["name"]: {key: copy.deepcopy(sink.get(key))
                       for key in ("active_port", "volume", "mute")}
        for sink in sinks
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hardware", action="store_true")
    args = parser.parse_args()
    prefix = "bluzy_test_" + uuid.uuid4().hex[:8]
    modules = []
    players = []
    default = pactl("get-default-sink")
    original_sinks = shared_state(listing("sinks"))
    original_profiles = {card["name"]: card.get("active_profile") for card in listing("cards")}
    original_streams = {
        stream["index"]: (stream["sink"], copy.deepcopy(stream.get("volume")))
        for stream in listing("sink-inputs")
    }

    class IsolatedTestOutputs(PulseAudioOutputs):
        def _bluetooth_streams(self):
            # Exercise production discovery/routing while protecting a real
            # phone that may start streaming during this test.
            return [stream for stream in super()._bluetooth_streams()
                    if stream.get("properties", {}).get("node.name", "").startswith(
                        "bluez_input." + prefix)]

    backend = IsolatedTestOutputs()

    def play(name, target):
        expected_sink = next(s["index"] for s in listing("sinks") if s["name"] == target)
        player = subprocess.Popen([
            "pw-cat", "--playback", "--raw", "--rate", "48000",
            "--channels", "2", "--target", target,
            "--properties", json.dumps({
                "node.name": name, "application.name": name, "application.id": name,
            }), "/dev/zero",
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        players.append(player)
        stream = wait_for(lambda: next((s for s in listing("sink-inputs")
                         if s.get("properties", {}).get("node.name") == name
                         and s["sink"] == expected_sink), None))
        return player, stream

    def assert_isolated():
        assert pactl("get-default-sink") == default, "Desktop default changed"
        profiles = {card["name"]: card.get("active_profile") for card in listing("cards")}
        assert profiles == original_profiles, "An audio card profile changed"
        current = shared_state(listing("sinks"))
        for name, values in original_sinks.items():
            assert current.get(name) == values, f"Shared device settings changed: {name}"
        for stream in listing("sink-inputs"):
            if stream["index"] in original_streams:
                assert (stream["sink"], stream.get("volume")) == original_streams[stream["index"]], "Existing application audio changed"

    try:
        if args.hardware:
            sinks = [s for s in listing("sinks") if s["name"] != "auto_null"]
            assert len(sinks) >= 2, "Need two independently routable hardware outputs"
            names = [s["name"] for s in sinks]
        else:
            names = [prefix + "_a", prefix + "_b"]
            for name in names:
                modules.append(pactl("load-module", "module-null-sink",
                                     f"sink_name={name}",
                                     f"sink_properties=device.description={name}"))
            wait_for(lambda: all(any(s["name"] == name for s in listing("sinks")) for name in names))
        indices = {s["name"]: s["index"] for s in listing("sinks")}
        phone_player, phone = play("bluez_input." + prefix, names[0])
        unrelated = [play(prefix + ".other." + str(i), name)[1]
                     for i, name in enumerate(names)]
        # Deliberately switch in both directions, including the default output.
        for destination in [*reversed(names), *names]:
            backend.select(OutputTarget(destination), route=True)
            wait_for(lambda: any(s["index"] == phone["index"] and
                     s["sink"] == indices[destination] for s in listing("sink-inputs")))
            streams = {s["index"]: s for s in listing("sink-inputs")}
            for other in unrelated:
                assert streams[other["index"]]["sink"] == other["sink"], (
                    f"Unrelated stream {other['index']} moved from {other['sink']} "
                    f"to {streams[other['index']]['sink']}"
                )
                assert streams[other["index"]]["volume"] == other["volume"]
            assert_isolated()
            print("PASS: Bluetooth-like playback ->", destination,
                  "(default, ports, device volume and other apps unchanged)")
        backend.set_volume(37, route=True)
        wait_for(lambda: any(s["index"] == phone["index"] and
                 all(round(v["value"] * 100 / 65536) == 37
                     for v in s["volume"].values()) for s in listing("sink-inputs")))
        assert_isolated()
        print("PASS: only Bluetooth-like stream volume changed")
        phone_player.terminate()
        phone_player.wait(timeout=5)
        _, phone = play("bluez_input." + prefix + ".reconnected", names[0])
        backend.refresh(route=True)
        wait_for(lambda: any(s["index"] == phone["index"] and
                 s["sink"] == indices[names[-1]] and
                 all(round(v["value"] * 100 / 65536) == 37
                     for v in s["volume"].values()) for s in listing("sink-inputs")))
        assert_isolated()
        print("PASS: reconnected stream retained output and its own volume")
        for player in players:
            player.terminate()
            player.wait(timeout=5)
        players.clear()
        if not args.hardware:
            pactl("unload-module", modules.pop())
            wait_for(lambda: not any(s["name"] == names[-1] for s in listing("sinks")))
            state = backend.refresh()
            assert state.missing and state.effective_sink == default
            assert state.preferred == OutputTarget(names[-1])
            modules.append(pactl("load-module", "module-null-sink", f"sink_name={names[-1]}"))
            wait_for(lambda: any(s["name"] == names[-1] for s in listing("sinks")))
            assert not backend.refresh().missing
            assert_isolated()
            print("PASS: unplug/replug fallback preserved preference and global settings")
    finally:
        for player in players:
            player.terminate()
            player.wait(timeout=5)
        for module in reversed(modules):
            subprocess.run(["pactl", "unload-module", module],
                           check=False, capture_output=True, timeout=5)
    print("All isolation checks passed; temporary test objects removed.")


if __name__ == "__main__":
    main()
