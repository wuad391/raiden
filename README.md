# Raiden

End-to-end data-collection toolkit for YAM robot arms. Covers calibration,
teleoperation, multi-camera recording, dataset conversion, sharding, and
visualization.

**[Documentation](https://tri-ml.github.io/raiden/)** · **[Get started](https://tri-ml.github.io/raiden/guide/)**

> **Fork notice.** This is a fork of [tri-ml/raiden](https://github.com/tri-ml/raiden)
> maintained for human-data-collection workflows. The upstream README features
> apply unchanged; this fork adds a single-pedal subtask-marking workflow
> and microphone narration. See [Fork changes](#fork-changes) below.

## What it does

- **Teleop** — leader-follower or SpaceMouse end-effector control, bimanual or single-arm.
- **Multi-camera recording** — mix ZED and Intel RealSense in one session, scene + wrist roles.
- **Subtask annotation** — foot pedal logs timestamped boundaries during a recording.
- **Microphone narration** — optional continuous audio capture, segmented by pedal presses.
- **Calibration** — automated hand-eye + scene extrinsics via ChArUco boards.
- **Depth backends** — RealSense IR, ZED SDK stereo, TRI Stereo, [Fast Foundation Stereo](https://github.com/NVlabs/Fast-FoundationStereo).
- **Manipulability-aware IK** — [PyRoki](https://github.com/chungmin99/pyroki) + [J-Parse](https://jparse-manip.github.io/).
- **Dataset output** — flat per-frame format with synced cameras, extrinsics, interpolated joint poses.
- **Metadata console** — `rd console` for reviewing demos and correcting labels.
- **Fin-ray gripper support** — see [docs](https://tri-ml.github.io/raiden/guide/hardware/#fin-ray-gripper).

## Install

See the **[installation guide](https://tri-ml.github.io/raiden/guide/installation/)**.

Optional extras for this fork:

```bash
uv sync --extra audio                      # microphone narration (PyAudio)
sudo apt install portaudio19-dev           # only on fresh Ubuntu, if PyAudio fails to build
```

## Commands

| Command | What it does |
|---|---|
| `rd list_devices` | Enumerate cameras, robot arms, SpaceMouse, microphones |
| `rd record_calibration_poses` | Record robot poses for camera calibration |
| `rd calibrate` | Hand-eye + scene-extrinsic calibration |
| `rd teleop` | Teleoperate without recording |
| `rd record` | Record demonstrations (add `--record-audio` for narration) |
| `rd replay` | Replay recorded follower-arm motion |
| `rd console` | Browse / correct demonstration metadata |
| `rd convert` | Convert raw recordings to a structured dataset |
| `rd shardify` | Pack converted episodes into WebDataset shards |
| `rd visualize` | View a converted recording in Rerun |
| `rd serve` | Policy-server inference endpoint |
| `rd make_ffs_onnx` | Export Fast Foundation Stereo ONNX / TensorRT engines |
| `rd make_tri_stereo_engine` | Compile TRI Stereo TensorRT engine from ONNX |

`rd <command> --help` for full options.

## Recording workflow

Per-episode operator script under this fork:

```
1. Press leader-arm button (or Enter, in spacemouse mode) → recording starts.
2. Press the foot pedal at each subtask boundary while performing the task.
3. Press leader-arm button (or Enter) again → recording stops.
4. Verdict prompt appears: press Enter (success) or f (failure).
```

Verdict keys:

| Key | Result | Stored as |
|---|---|---|
| `Enter` | success | `status: "success"` |
| `f` / `F` | failure | `status: "failure"` |
| any other key | skip | `status: "pending"` |
| 30 s timeout | skip | `status: "pending"` |
| Ctrl-C / e-stop during episode | forced failure | `status: "failure"` |

The verdict is written into the SQLite metadata DB. Only `status: "success"`
demos pass through `rd convert` into the trained dataset. To relabel later,
use `rd console` (terminal UI).

## Fork changes

Tracked from upstream merge base `2353b10` ("Fixed a minor bug in IK").

### Single-pedal subtask boundaries (always on)

Press the foot pedal during a recording to log a timestamped marker.

```json
"event_markers": [
  {"t": 1700000000123456789, "elapsed_s": 1.23, "clock": "camera"},
  ...
]
```

- `t` is on the same nanosecond clock as `robot_data.npz` and the converted
  camera frames — joinable without offset math.
- `clock` is `"camera"` on a healthy ZED setup; `"wallclock_fallback"` on the
  rare frame where the camera-clock read failed (so downstream consumers can
  detect and skip those).
- The pedal is **dedicated to subtask boundaries** — no pedal verdict, no
  pedal soft-pause, no `--marking-mode` flag. Verdict is on the keyboard:
  `Enter` = success, `f` = failure.
- Schema and full pedal table:
  [`docs/guide/recording.md`](docs/guide/recording.md) §"Subtask boundaries
  during a trajectory".

### `rd record --record-audio` (optional)

Capture microphone narration aligned with the pedal markers.

```bash
rd record --record-audio
rd record --record-audio --audio-device-index 3   # pick a non-default mic
```

- Mic stream opens at episode start; **audio before the first pedal press
  is treated as warm-up noise and discarded.**
- From the first press onwards, two shapes land under
  `<recording_dir>/audio/`:
  - `audio_full.wav` — continuous, first-press → end-of-episode.
  - `audio_<i>_HHMMSS.wav` — one per inter-press interval (sample-aligned
    slices of `audio_full`).
- `metadata.json` gains `audio_full` (a dict) and `audio_segments` (a list).
  Boundaries are on the same camera clock as `event_markers[*].t`.
- Episodes with zero pedal presses produce no audio files.
- Without the optional `audio` extra installed, the flag is silently skipped
  with a warning — recording continues unaffected.
- Schema and full layout:
  [`docs/guide/recording.md`](docs/guide/recording.md) §"Audio narration
  aligned with event markers".

### Downstream propagation

`event_markers`, `audio_full`, and `audio_segments` flow through the rest of
the pipeline:

- **`rd convert`** copies the keys into each converted episode's
  `metadata.json` and mirrors the raw `audio/` folder next to it.
- **`rd shardify`** writes `shards/subtask_index.json` keyed by `episode_id`
  and mirrors per-episode audio under `shards/audio/<episode_id>/`. Per-sample
  shard metadata already carries `episode_id`, so trainers join directly.

### `rd list_devices` enumerates microphones

Added a "Microphones" section listing PyAudio input devices with their
indices, channels, sample rate, and which one is the default. Use the index
with `--audio-device-index`. When PyAudio is not installed the section
prints `(none) — install PyAudio with: uv sync --extra audio`.

### TRI Stereo bundled weights removed

`weights/tri_stereo/stereo_c{32,64}.onnx{,.data}` were broken LFS pointers
and have been deleted (only `weights/tri_stereo/.gitkeep` remains). The
backend code ([`raiden/depth/tri_stereo.py`](raiden/depth/tri_stereo.py))
and the `rd make_tri_stereo_engine` command are unchanged. To use TRI
Stereo on this fork, drop your own ONNX weights into
`~/.config/raiden/weights/tri_stereo/` (preferred) or pass explicit
`--onnx` / `--engine` paths to the engine builder. Error messages link
back to this section.

ZED SDK and Fast Foundation Stereo backends are unchanged from upstream.

## Roadmap (upstream)

- Policy training and inference integration.
- LeRobot dataset format converter.
- Initial scene condition management in the console.

## Disclaimer

Raiden is research software provided **as-is**, without warranty of any kind.
Operating robotic arms involves inherent physical risks. The authors and
Toyota Research Institute accept **no liability** for any damage to property,
equipment, or persons arising from the use of this software.

## Citation

```bibtex
@misc{raiden2026,
  title  = {{RAIDEN}: A Toolkit for Policy Learning with {YAM} Bimanual Robot Arms},
  author = {Iwase, Shun and Miller, Patrick and Yao, Jonathan and Jatavallabhula, {Krishna Murthy} and Zakharov, Sergey},
  year   = {2026},
}
```
