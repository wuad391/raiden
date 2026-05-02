"""Single-pedal routing — every press during an active recording latches
the subtask Event. Pre-recording presses are no-ops.

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


# ---------------------------------------------------------------------------
# During recording — every pedal code latches the subtask Event
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("which", ["left", "middle", "right"])
def test_recording_any_pedal_latches_subtask(
    any_interface, fake_pedal, pedal_codes, robot_controller, which
):
    """Any pedal press during an active recording sets ``_pedal_subtask``.

    The recorder thread polls this latch, captures the camera clock
    once, and fans the timestamp out to ``add_event_marker`` and
    ``AudioRecorder.mark_boundary`` so both consumers see bit-identical
    timestamps.
    """
    any_interface.set_active_recording(robot_controller)
    fake_pedal.press(pedal_codes[which])
    assert any_interface.poll_subtask(robot_controller) is True


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
