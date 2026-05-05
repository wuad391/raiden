"""Operator-run hardware smoke checklist for the foot-pedal workflow.

Single-pedal model. This file is intentionally not an automated test —
it requires real hardware (foot pedal + cameras + robots) and a human
in the loop. Marked `pytest.skip(...)` so CI passes; discoverable via
`pytest --collect-only` so the procedure stays version-controlled.

Run before the 50-episode validation batch (TRI discussion §"Quick
Joint Validation Plan"). Aligned with that plan: 5 objects, 2 bins,
one simple skill, low cognitive load.
"""

from __future__ import annotations

import pytest


@pytest.mark.skip(reason="hardware required; run manually before TRI batch")
def test_operator_smoke_5_episode_warmup() -> None:
    """Five-episode warm-up before the 50-episode adversarial run.

    Per-episode procedure
    ---------------------
    1. Press leader-arm button (or Enter, in spacemouse mode) → recording
       starts.
    2. Press the foot pedal at each intended subtask boundary while
       performing the skill.

       NOTE: confirm the marker-count convention with TRI before
       running. The current implementation records anonymous markers
       (one dict per press). If downstream training expects
       `subtask_start` / `subtask_end` labels, raise this in the
       discussion thread before the warm-up.
    3. Press leader-arm button (or Enter) again → trajectory ends; verdict
       prompt appears.
    4. Press Enter on the keyboard → success verdict (or 'f' for failure).

    Pass criteria for the 5-episode warm-up
    ---------------------------------------
    - No pedal press is dropped in normal-speed use (operator never
      sees a press fail to register).
    - Each episode's `metadata.json` has the expected `event_markers`
      count (one entry per pedal press).
    - Each `event_markers[*].clock` is `"camera"` (no
      `"wallclock_fallback"` entries on a healthy ZED setup; one or
      two fallbacks is acceptable but should be investigated).
    - The pedal does NOT auto-start, end, or verdict any episode — it
      is dedicated to subtask boundaries during recording.
    - Operator does not need to look away from the task to recover
      state.

    With `--record-audio`
    ---------------------
    - First pedal press in an episode starts the audio stream (visible
      in the "Audio recording started" log line).
    - Each episode's `audio_segments` count in `metadata.json` matches
      the `event_markers` count.
    - Each `audio/*.wav` plays back as audible mono 48 kHz PCM.
    - `audio_segments[i].boundary_t_ns` matches `event_markers[i].t`
      (within a few ms — they share the same camera-clock read).

    If all 5 episodes pass these checks, proceed to the 50-episode
    batch. If any check fails, stop and file the failure mode (which
    pedal, what was expected, what happened) before the batch begins.

    Cross-references
    ----------------
    - docs/guide/recording.md §"Subtask boundaries during a trajectory"
      — operator-facing pedal mapping.
    - docs/guide/safety.md §"Foot pedal — mode-dependent behavior"
      — confirms pedal during recording is dedicated to subtask
      boundaries (no soft e-stop side-effect).
    """
