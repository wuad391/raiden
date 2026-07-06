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

# Load the shared identifiers/scan by file path: pedal_constants.py is
# stdlib-only, and skipping the `raiden` package import keeps this script
# runnable before the project's dependencies are installed.
import importlib.util

_constants_path = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..",
    "raiden",
    "robot",
    "pedal_constants.py",
)
_spec = importlib.util.spec_from_file_location("pedal_constants", _constants_path)
_constants = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_constants)

DEVICE_NAME_HINT = _constants.DEVICE_NAME
PEDAL_LEFT = _constants.PEDAL_LEFT
PEDAL_MIDDLE = _constants.PEDAL_MIDDLE
PEDAL_RIGHT = _constants.PEDAL_RIGHT
find_pedal_event_devices = _constants.find_pedal_event_devices

EV_KEY = 1
KEY_DOWN = 1
EVENT_STRUCT = struct.Struct("llHHI")


def _matching_devices() -> list[tuple[str, str]]:
    return [(d["path"], d["name"]) for d in find_pedal_event_devices()]


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

    # The pedal registers several input interfaces whose names all contain
    # the hint; only one carries the key events. Listen on all of them.
    fds = []
    for path, name in devices:
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
        except PermissionError:
            print(f"\nPermission denied opening {path}.")
            print(
                "Install the udev rule first:\n"
                "  sudo bash scripts/install_footpedal_udev.sh\n"
                "Then unplug/replug the pedal, and log out and back in if the\n"
                "script just added you to the 'input' group."
            )
            for f, _ in fds:
                os.close(f)
            return 1
        except OSError as e:
            print(f"  ! Could not open {path}: {e}")
            continue
        fds.append((fd, path))
        print(f"  ✓ FootPedal opened: {name} ({path})")
    if not fds:
        return 1

    code_to_label = {
        PEDAL_LEFT: "LEFT",
        PEDAL_MIDDLE: "MIDDLE",
        PEDAL_RIGHT: "RIGHT",
    }
    print(f"\nListening for {seconds}s — press any pedal button.  Ctrl-C to stop.\n")
    sel = selectors.DefaultSelector()
    for fd, path in fds:
        sel.register(fd, selectors.EVENT_READ, data=path)
    deadline = time.monotonic() + seconds
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            for key, _ in sel.select(timeout=remaining):
                try:
                    data = os.read(key.fd, EVENT_STRUCT.size * 64)
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
        for fd, _ in fds:
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
