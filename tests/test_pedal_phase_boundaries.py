"""Cross-phase isolation — the BUG-2 surface area.

`drain_pedal_events` exists to keep the `threading.Event`-backed
subtask latch from bleeding across phase boundaries. Single-pedal +
single-Event model: only one latch to manage.
"""

from __future__ import annotations


def test_drain_clears_subtask_latch(any_interface, robot_controller):
    any_interface._pedal_subtask.set()
    any_interface.drain_pedal_events(robot_controller)
    assert any_interface.poll_subtask(robot_controller) is False


def test_stray_press_before_recording_does_not_latch(
    any_interface, fake_pedal, pedal_codes, robot_controller
):
    """BUG-2 regression: a stray pedal press during the 'Press Enter to START'
    wait must not bleed into the new episode.

    Under the single-pedal model the callback is gated on
    ``_recording_controller is not None``, so a pre-recording press is a
    no-op at the source — but ``drain_pedal_events`` at episode start
    remains load-bearing if a press arrives in the sub-millisecond
    window between ``set_active_recording`` and the first
    ``poll_subtask`` from the recording loop.
    """
    fake_pedal.press(pedal_codes["middle"])  # stray, ignored (no recording)
    any_interface.set_active_recording(robot_controller)
    any_interface.drain_pedal_events(robot_controller)
    assert any_interface.poll_subtask(robot_controller) is False


def test_press_during_recording_then_phase_change_clears_on_drain(
    any_interface, fake_pedal, pedal_codes, robot_controller
):
    """A legitimate subtask press latches during recording; once the episode
    ends the next ``drain_pedal_events`` clears any unread residual."""
    any_interface.set_active_recording(robot_controller)
    fake_pedal.press(pedal_codes["middle"])
    # Episode ends without the recorder having drained.
    any_interface.set_active_recording(None)
    any_interface.drain_pedal_events(robot_controller)
    assert any_interface.poll_subtask(robot_controller) is False
