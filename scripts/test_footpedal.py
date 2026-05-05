"""Standalone foot-pedal verification.

Use after running ``sudo bash scripts/install_footpedal_udev.sh`` (and
unplugging/replugging the device) to confirm the pedal opens without
sudo and produces key-down events.

Usage::

    python scripts/test_footpedal.py            # listen for 20 seconds
    python scripts/test_footpedal.py --list     # just enumerate matching devices
    python scripts/test_footpedal.py --seconds 60
"""

from __future__ import annotations

import argparse
import os
import selectors
import struct
import sys
import time
from pathlib import Path

DEVICE_NAME_HINT = "PCsensor FootSwitch Keyboard"
EV_KEY = 1
KEY_DOWN = 1
PEDAL_LEFT = 30  # KEY_A
PEDAL_MIDDLE = 48  # KEY_B
PEDAL_RIGHT = 46  # KEY_C
EVENT_STRUCT = struct.Struct("llHHI")


def _matching_devices() -> list[tuple[str, str]]:
    found = []
    for d in sorted(
        Path("/sys/class/input").glob("event*"),
        key=lambda p: int(p.name[5:]),
    ):
        name_file = d / "device" / "name"
        if not name_file.exists():
            continue
        try:
            name = name_file.read_text().strip()
        except OSError:
            continue
        if DEVICE_NAME_HINT.lower() in name.lower():
            found.append((f"/dev/input/{d.name}", name))
    return found


def list_devices() -> int:
    """Print every /sys/class/input/event* entry whose device name matches."""
    print(f"Scanning /sys/class/input for '{DEVICE_NAME_HINT}'...")
    found = _matching_devices()
    if not found:
        print("  (none) — make sure the foot pedal is plugged in.")
        print("  If you just installed the udev rule, unplug the pedal and replug it.")
        return 1
    for path, name in found:
        print(f"  {path:<22}  {name}")
    return 0


def listen(seconds: int) -> int:
    """Open the pedal and print every key-down event for *seconds* seconds."""
    devices = _matching_devices()
    if not devices:
        print(f"FootPedal ({DEVICE_NAME_HINT!r}) not found.")
        print("Make sure it is plugged in.")
        print(
            "If this is the first run, install the udev rule first:\n"
            "  sudo bash scripts/install_footpedal_udev.sh"
        )
        return 1

    path, name = devices[0]
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    except (RuntimeError, PermissionError) as e:
        print(f"\nFailed to open foot pedal {path}: {e}")
        if isinstance(e, PermissionError):
            print(
                "Permission denied — install the udev rule first:\n"
                "  sudo bash scripts/install_footpedal_udev.sh\n"
                "Then unplug and replug the foot pedal."
            )
        return 1

    code_to_label = {
        PEDAL_LEFT: "LEFT",
        PEDAL_MIDDLE: "MIDDLE",
        PEDAL_RIGHT: "RIGHT",
    }
    print(f"  ✓ FootPedal opened: {name} ({path})")
    print(f"\nListening for {seconds}s — press any pedal button.  Ctrl-C to stop.\n")
    sel = selectors.DefaultSelector()
    sel.register(fd, selectors.EVENT_READ)
    deadline = time.monotonic() + seconds
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            for _, _ in sel.select(timeout=remaining):
                try:
                    data = os.read(fd, EVENT_STRUCT.size * 64)
                except BlockingIOError:
                    continue
                for i in range(0, len(data) - EVENT_STRUCT.size + 1, EVENT_STRUCT.size):
                    _, _, event_type, code, value = EVENT_STRUCT.unpack_from(data, i)
                    if event_type == EV_KEY and value == KEY_DOWN:
                        label = code_to_label.get(code, "?")
                        print(f"  press: code={code} ({label}) at t={time.time():.3f}")
    except KeyboardInterrupt:
        pass
    finally:
        sel.close()
        os.close(fd)
    print("\nDone.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--list",
        action="store_true",
        help="just list matching /sys/class/input devices and exit",
    )
    ap.add_argument(
        "--seconds",
        type=int,
        default=20,
        help="how long to listen for presses (default: 20)",
    )
    args = ap.parse_args()
    if args.list:
        return list_devices()
    return listen(args.seconds)


if __name__ == "__main__":
    sys.exit(main())
