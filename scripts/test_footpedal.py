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
import sys
import time
from pathlib import Path

DEVICE_NAME_HINT = "PCsensor FootSwitch Keyboard"


def list_devices() -> int:
    """Print every /sys/class/input/event* entry whose device name matches."""
    print(f"Scanning /sys/class/input for '{DEVICE_NAME_HINT}'...")
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

    if not found:
        print("  (none) — make sure the foot pedal is plugged in.")
        print("  If you just installed the udev rule, unplug the pedal and replug it.")
        return 1
    for path, name in found:
        print(f"  {path:<22}  {name}")
    return 0


def listen(seconds: int) -> int:
    """Open the pedal and print every key-down event for *seconds* seconds."""
    try:
        from raiden.robot.footpedal import (
            FootPedal,
            PEDAL_LEFT,
            PEDAL_MIDDLE,
            PEDAL_RIGHT,
        )
    except ImportError as e:
        print(f"Failed to import raiden.robot.footpedal: {e}")
        print(
            "Run from a checkout with the project installed (e.g. "
            "`uv tool install -e .` from the repo root)."
        )
        return 1

    pedal = FootPedal()
    try:
        pedal.open()
    except (RuntimeError, PermissionError) as e:
        print(f"\nFootPedal.open() failed: {e}")
        if isinstance(e, PermissionError):
            print(
                "Permission denied — install the udev rule first:\n"
                "  sudo bash scripts/install_footpedal_udev.sh\n"
                "Then unplug and replug the foot pedal."
            )
        return 1

    code_to_label = {PEDAL_LEFT: "LEFT", PEDAL_MIDDLE: "MIDDLE", PEDAL_RIGHT: "RIGHT"}

    def _on_press(code: int) -> None:
        label = code_to_label.get(code, "?")
        print(f"  press: code={code} ({label}) at t={time.time():.3f}")

    pedal.on_press(_on_press)
    pedal.start()

    print(f"\nListening for {seconds}s — press any pedal button.  Ctrl-C to stop.\n")
    try:
        time.sleep(seconds)
    except KeyboardInterrupt:
        pass
    finally:
        pedal.close()
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
