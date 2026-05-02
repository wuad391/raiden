"""Cross-phase isolation — the BUG-2 surface area.

`drain_pedal_events` exists to keep `threading.Event` latches from
bleeding across phase boundaries. Single-pedal model: only two latches
to manage (recorder-side + audio-side).
"""

from __future__ import annotations


def _set_both_subtask_events(interface):
    """Latch both subtask Events manually so the drain has work to do."""
    interface._pedal_subtask.set()
    interface._pedal_subtask_audio.set()


def test_drain_clears_both_subtask_latches(any_interface, robot_controller):
    _set_both_subtask_events(any_interface)
    any_interface.drain_pedal_events(robot_controller)
    assert any_interface.poll_subtask(robot_controller) is False
    assert any_interface.poll_subtask_audio(robot_controller) is False


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
    assert any_interface.poll_subtask_audio(robot_controller) is False


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
    assert any_interface.poll_subtask_audio(robot_controller) is False
