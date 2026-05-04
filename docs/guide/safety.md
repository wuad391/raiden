# Safety

!!! warning "Disclaimer"
    Raiden is research software provided **as-is**, without warranty of any kind.
    Operating robotic arms involves inherent physical risks. The authors and
    Toyota Research Institute accept **no liability** for any damage to property,
    equipment, or persons arising from the use of this software. Users are solely
    responsible for ensuring safe operating conditions, including appropriate
    physical barriers, trained operators, and compliance with all applicable
    safety regulations.

## Gripper Control

!!! warning "Gripper — Risk of Mechanical Damage"
    During leader-follower teleoperation the follower gripper position is
    controlled by mapping the leader trigger encoder **directly** (0 → fully
    open, 1 → fully closed) to the follower gripper.  **Commanding a value of
    1.0 (trigger fully depressed) drives the gripper fingers to their hard stop
    and can break the gripper fingers.**  Do not hold the trigger fully
    depressed.

## Foot pedal — mode-dependent behavior

The same USB foot switch hardware drives different behavior depending on
which command is running.

| Command | LEFT pedal | MIDDLE pedal | RIGHT pedal | Emergency stop |
|---|---|---|---|---|
| `rd record` | subtask boundary marker | subtask boundary marker | subtask boundary marker | `Ctrl-C` |
| `rd teleop` | soft e-stop (5 s hold → home → exit) | (ignored) | (ignored) | `Ctrl-C` / soft e-stop |
| `rd serve` | hard e-stop (immediate, server exits) | (ignored) | (ignored) | `Ctrl-C` / left pedal |

**During `rd record`** (the recording workflow): each pedal press logs a
timestamped subtask-boundary marker into `event_markers` (and into
`audio_segments` when `--record-audio` is on). The pedal does NOT trigger
a soft e-stop in this mode — emergency stop is `Ctrl-C`. See
[Recording](recording.md#subtask-boundaries-during-a-trajectory).

**During `rd teleop`** (no recording): the LEFT pedal is the soft e-stop
described below. MIDDLE and RIGHT are no-ops.

**During `rd serve`** (policy evaluation): the LEFT pedal triggers an
immediate hard e-stop (`emergency_stop()`); the server exits.

### Soft e-stop (teleop only)

!!! warning "Soft E-Stop Disclaimer"
    This is a **software-level ("soft") e-stop only**. YAM arms do not have brakes
    in their motors. When activated, the software holds all arm joints at their
    current positions for 5 seconds before commanding them to the home position and
    exiting the session. It does **not** cut motor power or guarantee immediate
    mechanical stoppage. Do not rely on this as a primary or sole safety mechanism.

Behavior during `rd teleop`:

1. Press the LEFT foot pedal.
2. All active arms freeze at their current positions.
3. After 5 seconds, all arms return to the home position and the session exits cleanly.
4. Pressing the LEFT pedal again during the 5-second hold resets the countdown.

### Setup

The foot switch is a USB HID device. The first time you use it, install a
udev rule to grant read access without requiring `sudo`:

```bash
sudo bash scripts/install_footpedal_udev.sh
```

This only needs to be run once. After that, the foot switch is auto-detected
when Raiden starts.

The foot switch is **optional** for `rd record` (recording continues
without subtask markers if the pedal isn't connected — Raiden prints a
warning) and for `rd teleop` / `rd serve` (no soft / hard e-stop available).

### Hardware

| Component | Qty | Link |
|---|---|---|
| PCsensor USB Foot Switch | 1 | [Amazon](https://a.co/d/04osof8S) |
