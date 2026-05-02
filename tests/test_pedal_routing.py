"""Single-pedal routing — every press during an active recording latches both
the subtask and the subtask-audio Events. Pre-recording presses are no-ops.

Parametrised over both production interfaces (SpaceMouse, YAM).
"""

from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# Pre-recording — pedal callback is gated on `_recording_controller is not None`
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("which", ["left", "middle", "right"])
def test_pre_recording_pedal_press_is_a_no_op(
    any_interface, fake_pedal, pedal_codes, robot_controller, which
):
    """Before set_active_recording, no pedal code latches anything.

    Guards against BUG-1 (AttributeError on press before recording).
    """
    fake_pedal.press(pedal_codes[which])
    assert any_interface.poll_subtask(robot_controller) is False
    assert any_interface.poll_subtask_audio(robot_controller) is False


# ---------------------------------------------------------------------------
# During recording — every pedal code latches both Events
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("which", ["left", "middle", "right"])
def test_recording_any_pedal_latches_both_subtask_events(
    any_interface, fake_pedal, pedal_codes, robot_controller, which
):
    """Any pedal press during an active recording sets BOTH the recorder
    latch (poll_subtask) and the audio latch (poll_subtask_audio).

    Both must latch independently — they are consumed by separate
    threads (the recorder loop drains poll_subtask; AudioRecorder
    drains poll_subtask_audio).
    """
    any_interface.set_active_recording(robot_controller)
    fake_pedal.press(pedal_codes[which])
    assert any_interface.poll_subtask(robot_controller) is True
    assert any_interface.poll_subtask_audio(robot_controller) is True


def test_subtask_latch_is_clear_on_read(
    any_interface, fake_pedal, pedal_codes, robot_controller
):
    """A second poll without a new press returns False (clear-on-read)."""
    any_interface.set_active_recording(robot_controller)
    fake_pedal.press(pedal_codes["middle"])
    assert any_interface.poll_subtask(robot_controller) is True
    assert any_interface.poll_subtask(robot_controller) is False


def test_two_presses_count_as_two_subtask_boundaries(
    any_interface, fake_pedal, pedal_codes, robot_controller
):
    """Successive presses each latch — but threading.Event collapses
    repeats between drains. The recorder polls every 50 ms, so the
    operationally relevant invariant is: each press observed by a
    *drained* state latches one boundary."""
    any_interface.set_active_recording(robot_controller)
    fake_pedal.press(pedal_codes["middle"])
    assert any_interface.poll_subtask(robot_controller) is True
    fake_pedal.press(pedal_codes["middle"])
    assert any_interface.poll_subtask(robot_controller) is True
