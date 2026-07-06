"""Shared PCsensor FootSwitch identifiers and device discovery.

Stdlib-only (no evdev) so every pedal consumer — the reader in
``raiden.robot.footpedal``, ``rd list_devices``, and
``scripts/test_footpedal.py`` — agrees on what counts as the pedal.
"""

from pathlib import Path
from typing import Dict, List

DEVICE_NAME = "PCsensor FootSwitch Keyboard"

# Default key codes emitted by the 3-pedal PCsensor FootSwitch.
# Adjust if your device is configured differently (use `evtest` to confirm).
PEDAL_LEFT = 30  # KEY_A
PEDAL_MIDDLE = 48  # KEY_B
PEDAL_RIGHT = 46  # KEY_C


def find_pedal_event_devices() -> List[Dict[str, str]]:
    """Scan /sys/class/input for devices matching ``DEVICE_NAME``.

    Reads sysfs only, so it works before the udev rule is installed.
    Returns ``[{"path": "/dev/input/eventN", "name": ...}, ...]``.
    """
    found: List[Dict[str, str]] = []
    for event_dir in sorted(
        Path("/sys/class/input").glob("event*"),
        key=lambda p: int(p.name[5:]),
    ):
        name_file = event_dir / "device" / "name"
        if not name_file.exists():
            continue
        try:
            name = name_file.read_text().strip()
        except OSError:
            continue
        if DEVICE_NAME.lower() in name.lower():
            found.append({"path": f"/dev/input/{event_dir.name}", "name": name})
    # The pedal registers several interfaces (e.g. "... Keyboard Mouse",
    # "... Keyboard Consumer Control") whose names all contain the hint.
    # Put exact-name matches first — that interface carries the key events.
    found.sort(key=lambda d: d["name"].lower() != DEVICE_NAME.lower())
    return found
