"""Audio-pedal routing — single-pedal model.

Pins the contract that any pedal press during an active recording latches
both the recorder-side event (``poll_subtask``) and the audio-side event
(``poll_subtask_audio``). Pre-recording presses are no-ops at the source
(callback gates on ``_recording_controller is not None``).
"""

from __future__ import annotations


def test_subtask_audio_latch_set_when_recording(
    any_interface, fake_pedal, pedal_codes, robot_controller
):
    """Happy path: active recording + pedal press → audio latch fires."""
    any_interface.set_active_recording(robot_controller)
    any_interface.drain_pedal_events(robot_controller)

    fake_pedal.press(pedal_codes["middle"])
    # Both consumers see the press; each clears its own latch.
    assert any_interface.poll_subtask_audio(robot_controller) is True
    assert any_interface.poll_subtask(robot_controller) is True


def test_subtask_audio_latch_quiet_between_episodes(
    any_interface, fake_pedal, pedal_codes, robot_controller
):
    """No active recording → pedal press latches nothing."""
    # No set_active_recording → between-episode phase.
    fake_pedal.press(pedal_codes["middle"])
    assert any_interface.poll_subtask(robot_controller) is False
    assert any_interface.poll_subtask_audio(robot_controller) is False


def test_drain_pedal_events_clears_audio_latch(
    any_interface, fake_pedal, pedal_codes, robot_controller
):
    """The drain helper includes the audio channel so stale presses don't
    bleed across episode boundaries."""
    any_interface.set_active_recording(robot_controller)
    fake_pedal.press(pedal_codes["middle"])
    any_interface.drain_pedal_events(robot_controller)
    assert any_interface.poll_subtask(robot_controller) is False
    assert any_interface.poll_subtask_audio(robot_controller) is False
