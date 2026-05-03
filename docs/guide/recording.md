# Recording demonstrations

## Overview

The `rd record` command records a full teleoperation demonstration:

- **Camera data** at 30 fps - ZED cameras write `.svo2` files; RealSense
  cameras write `.bag` files. Both formats store the raw sensor stream
  (stereo pair + depth) and are converted to a structured dataset afterwards
  with `rd convert`.
- **Robot joint data** at ~100 Hz - both leader and follower joint positions
  and velocities are saved to `robot_data.npz`. Robot timestamps are recorded
  via the reference camera's clock:
  - **ZED cameras** use `sl.TIME_REFERENCE.IMAGE`, which returns the host
    wall-clock time at the moment the frame was captured. This is on the same
    clock as `time.time_ns()` and the frame timestamps in the SVO2 file.
    No correction is needed at conversion time.
  - **RealSense cameras** attempt to use `global_time_enabled` to stamp frames
    with host wall-clock time. Because support varies across D4xx firmware
    versions, Raiden also measures the offset between the first frame's
    hardware timestamp and `time.time_ns()` at recording start and stores it
    in `metadata.json` as `realsense_clock_offsets`. The converter applies this
    offset automatically as a fallback for older firmware.

All data for one recording episode lands in a single timestamped directory
under the output folder.

## Single-arm vs bimanual

Pass `--arms single` to record with the left arm only:

```bash
rd record --arms single
```

In single-arm mode the active arm is always named **left** for consistency.
All poses and extrinsics are expressed in the **left-arm base frame** - this
convention applies in both bimanual and single-arm setups.

## Thread layout

| Thread | Purpose |
|---|---|
| `teleop-right` | Follower-right mirrors leader-right at 100 Hz (runs for whole session) |
| `teleop-left` | Follower-left mirrors leader-left at 100 Hz (runs for whole session) |
| `camera-<name>` | One per camera; calls `camera.grab()` in a tight loop (SDK limits to 30 fps); active only during an episode |
| `robot-recorder` | Reads all joint observations at ~100 Hz and timestamps each sample using `camera.get_current_timestamp_ns()` (ZED SDK clock); active only during an episode |

## Output layout

```
<output_dir>/<task>_<timestamp>/
    metadata.json        # task name, duration, fps statistics
    robot_data.npz       # joint positions and velocities for all arms
    cameras/
        scene_camera.svo2
        left_wrist_camera.svo2
        right_wrist_camera.svo2
```

### `robot_data.npz` keys

Each robot arm contributes keys of the form `<arm>_<field>`, e.g.:

| Key | Shape | Description |
|---|---|---|
| `timestamps` | `(N,)` int64 | Absolute nanosecond timestamps from the ZED SDK clock. Directly comparable with the `timestamps.npy` files written by `rd convert`. |
| `leader_r_joint_pos` | `(N, 6)` float32 | Leader-right joint positions (rad) |
| `leader_r_joint_vel` | `(N, 6)` float32 | Leader-right joint velocities (rad/s) |
| `follower_r_joint_pos` | `(N, 7)` float32 | Follower-right joint positions including gripper (rad) |
| `follower_r_joint_vel` | `(N, 7)` float32 | Follower-right joint velocities (rad/s) |
| `follower_l_joint_pos` | `(N, 7)` float32 | Follower-left joint positions including gripper (rad) |

## Usage

```bash
# Leader-follower control (default)
rd record

# SpaceMouse EE-velocity control (paths loaded from ~/.config/raiden/spacemouse.json)
rd record --control spacemouse

# Single arm with SpaceMouse
rd record --control spacemouse --arms single

# Upload to S3 after each episode
rd record --s3-bucket my-robot-data --s3-prefix demonstrations

# Store data in a custom root directory (default: ./data)
rd record --data-dir /mnt/storage/robot_data
```

Episodes are written to `<data-dir>/raw/<task_name>/`.

!!! warning "Gripper — Risk of Mechanical Damage"
    The follower gripper position is mapped directly from the leader trigger
    (0 → open, 1 → closed).  **Fully depressing the trigger drives the fingers
    to their hard stop and can break the gripper.**  Do not hold the trigger
    fully depressed.  See [Safety](safety.md#gripper-control) for details.

During the session:

- Press the **button on any leader arm** (or **Enter** in non-leader modes)
  to start an episode; press it again to stop and save.
- After each episode, start the next one the same way, or press **`q`** to
  end the session.
- During recording, each **foot-pedal press** logs a subtask boundary into
  `event_markers` (and into `audio_segments` when `--record-audio` is on).
- Press **Ctrl-C** for an emergency stop.

## Marking demonstrations

After each episode, mark it on the keyboard. Only successful demonstrations
are included when you run `rd convert`.

| Key | Action |
|---|---|
| `Enter` | Mark as **Success** |
| `f` | Mark as **Failure** |
| any other key / 30 s timeout | Leave as `pending` |

You can correct the status later in the [console](console.md).

### Subtask boundaries during a trajectory

Each press of the connected foot pedal during an active recording logs a
timestamped marker into `metadata.json` under the `event_markers` key:

```json
"event_markers": [
  {"t": 1700000000123456789, "elapsed_s": 1.23, "clock": "camera"},
  ...
]
```

`t` is on the same clock as `timestamps` in `robot_data.npz` and the
converted camera frames. If the reference camera's clock read fails for a
given marker, the entry's `clock` field is set to `"wallclock_fallback"` and
`t` uses `time.time_ns()` instead — for ZED setups that single marker will
not align with frame timestamps; check `clock` before interpolating.

The pedal is dedicated to subtask boundaries — there is no pedal verdict and
no pedal soft-pause.

### Audio narration aligned with event markers (`--record-audio`)

Add `--record-audio` to capture microphone audio alongside the trajectory.

```bash
rd record --record-audio
```

The microphone stream opens at episode start, but **the period before
the first pedal press is treated as warm-up noise and discarded**. Both
`audio_full.wav` and the per-segment WAVs start at the first press.

| Pedal press during recording | Effect |
|---|---|
| **First** press in episode | Log event marker **and** anchor `audio_full.wav` at this instant. |
| **Subsequent** presses | Log event marker **and** mark a new audio segment boundary. |

When the episode ends, two kinds of WAV files are written:

- **`audio_full.wav`** — continuous recording from the first press to
  end-of-episode. Sample-identical to the concatenation of the
  per-segment WAVs.
- **`audio_<i>_HHMMSS.wav`** — one per inter-press interval. With N
  pedal presses you get N segments: segment `i` runs from press `i` to
  press `i+1` (or to end-of-episode for the last).

If the operator never presses the pedal during an episode, **no audio
files are written** for that episode.

PyAudio is an optional extra; install once with:

```bash
uv sync --extra audio
# Ubuntu may also need: sudo apt install portaudio19-dev
```

Without the extra, raiden prints a yellow warning and continues without
audio. The recording itself is not affected.

Outputs land under `<recording_dir>/audio/`:

```
audio/
  audio_full.wav                # first-press → end-of-episode, continuous
  audio_full.json               # sidecar with start_t_ns + clock + duration
  audio_0_YYYYMMDD_HHMMSS.wav   # segment 0 (first press → second press)
  audio_0_YYYYMMDD_HHMMSS.json  # sidecar with boundary_t_ns + clock + duration
  audio_1_YYYYMMDD_HHMMSS.wav
  audio_1_YYYYMMDD_HHMMSS.json
  ...
```

`metadata.json` gains `audio_full` (a single dict) and `audio_segments`
(a list, when there were presses):

```json
"audio_full": {
  "audio_file": "audio_full.wav",
  "start_t_ns": 1700000000000000000,
  "duration_s": 18.247,
  "clock": "camera"
},
"audio_segments": [
  {"segment_id": 0, "audio_file": "audio_0_...wav",
   "boundary_t_ns": 1700000000123456789, "duration_s": 4.523, "clock": "camera"},
  ...
]
```

`audio_full.start_t_ns` and each segment's `boundary_t_ns` are on the
same clock as `event_markers[*].t` and `timestamps` in `robot_data.npz`
(subject to the same `clock="wallclock_fallback"` caveat — check the
`clock` field before interpolating).

Sample format: 48 kHz mono 16-bit PCM. Default device: system default
microphone, override with `--audio-device-index <int>` (use `rd
list_devices` to enumerate inputs and pick an index).

### Downstream propagation

`event_markers`, `audio_full`, and `audio_segments` are preserved
through the full post-processing pipeline:

- **`rd convert`** copies all three keys into each converted episode's
  `metadata.json`, and mirrors the raw `audio/` folder next to the
  converted sequence so trainers can locate the WAVs without touching
  raw recordings.
- **`rd shardify`** writes a single `shards/subtask_index.json` keyed
  by `episode_id` (the converted episode's directory name). Each entry
  has `{"event_markers": [...], "audio_segments": [...], "audio_full": {...}}`
  (`audio_full` only when present). Per-episode audio is mirrored under
  `shards/audio/<episode_id>/` so the shard directory is self-contained.
- Per-sample shard metadata (`{uuid}.metadata.json`) already carries
  `episode_id`, so a trainer joins to the subtask index by that key.

After recording, convert the raw camera files with [rd convert](conversion.md).

## Uploading to S3

Pass `--s3-bucket` to automatically upload each episode to S3 immediately
after it is saved:

```bash
rd record --s3-bucket my-robot-data
```

By default episodes are uploaded under the `demonstrations/` prefix. Override
with `--s3-prefix`:

```bash
rd record --s3-bucket my-robot-data --s3-prefix pick_purrito/session_01
```

Each episode directory is uploaded recursively. The S3 key for each file is:

```
<prefix>/<episode_dir_name>/<file>
```

For example, with `--s3-prefix demonstrations` and episode directory
`pick_purrito_20260312_220000`:

```
demonstrations/pick_purrito_20260312_220000/metadata.json
demonstrations/pick_purrito_20260312_220000/robot_data.npz
demonstrations/pick_purrito_20260312_220000/cameras/scene_camera.svo2
demonstrations/pick_purrito_20260312_220000/cameras/left_wrist_camera.svo2
demonstrations/pick_purrito_20260312_220000/cameras/right_wrist_camera.svo2
```

**Credentials** — Raiden uses the standard AWS credential chain via `boto3`.
Configure credentials with any of the usual methods before recording:

```bash
# Option A — environment variables
export AWS_ACCESS_KEY_ID=...
export AWS_SECRET_ACCESS_KEY=...
export AWS_DEFAULT_REGION=us-east-1

# Option B — AWS CLI profile
aws configure
```

## RealSense bag file size

> **Warning:** RealSense `.bag` files are large. A 10-second recording at 30 fps (640×480 BGR8 color + 640×480 depth) typically produces **~500 MB–1 GB per camera**. For longer demonstrations or setups with multiple RealSense cameras, disk space can fill up quickly.
>
> **Mitigations:**
>
> - Reduce recording duration where possible.
> - Ensure the recording disk has sufficient free space before starting a session.
