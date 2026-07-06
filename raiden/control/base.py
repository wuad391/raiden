"""Abstract base class for teleoperation input methods."""

import threading
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from raiden.robot.footpedal import try_open_footpedal

if TYPE_CHECKING:
    from raiden.robot.controller import RobotController


class TeleopInterface(ABC):
    """Shared interface for all teleoperation input methods.

    To add a new input method, subclass this, implement all abstract methods,
    and instantiate it from the CLI or calling code.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Short identifier used in metadata (e.g. 'leader', 'spacemouse')."""

    @abstractmethod
    def setup(self, robot_controller: "RobotController") -> None:
        """Device-specific setup after robots are ready (attach hardware, warm up IK, etc.)."""

    @abstractmethod
    def start(self, robot_controller: "RobotController") -> None:
        """Start the control loop threads."""

    @abstractmethod
    def stop(self, robot_controller: "RobotController") -> None:
        """Stop the control loop threads."""

    @property
    @abstractmethod
    def banner(self) -> str:
        """Status message printed when teleoperation becomes active."""

    # ------------------------------------------------------------------
    # Optional overrides — defaults suit most future interfaces
    # ------------------------------------------------------------------

    def open(self) -> None:
        """Open session-level peripherals (footpedal, etc.).

        Called once per session before the first episode.  Default: opens
        the foot pedal with subtask-boundary semantics.  Subclasses that
        need additional session-level setup should call ``super().open()``
        first.
        """
        self._open_footpedal_for_subtask_latches()

    def close(self) -> None:
        """Close session-level peripherals.

        Called once at session end.  Default: closes the foot pedal if it
        was opened by ``open()``.
        """
        if getattr(self, "_footpedal", None) is not None:
            self._footpedal.close()
            self._footpedal = None

    def _open_footpedal_for_subtask_latches(self) -> None:
        """Initialise the subtask Event and bind the pedal callback.

        During an active recording, presses latch the subtask event that
        the recorder polls; outside a recording they latch
        ``_pedal_trigger``, which calibration consumes via
        ``poll_pedal_trigger`` as a hands-free trigger.
        """
        self._pedal_subtask = threading.Event()
        self._pedal_trigger = threading.Event()
        # Initialised before the footpedal thread is started so the
        # callback can never race a missing attribute on the first press.
        self._recording_controller = None
        self._footpedal = try_open_footpedal()
        if self._footpedal is None:
            return

        def _cb(_code: int) -> None:
            if self._recording_controller is not None:
                self._pedal_subtask.set()
            else:
                self._pedal_trigger.set()

        self._footpedal.on_press(_cb)
        self._footpedal.start()
        print("  ✓ FootPedal ready: press to mark a subtask boundary")

    def set_active_recording(self, robot_controller=None) -> None:
        """Notify the interface whether a recording episode is active.

        Pass the controller when an episode starts; pass None when it ends.
        Used by interfaces that want to gate pedal latches on the
        recording-active state.  Default: no-op.
        """
        self._recording_controller = robot_controller

    def poll_subtask(self, robot_controller: "RobotController") -> bool:
        """Return True if the operator pressed the subtask-boundary pedal
        during an active recording.

        Consumed by the recorder to capture a timestamp once (passed to
        both ``add_event_marker`` and ``AudioRecorder.mark_boundary`` so
        the two consumers see bit-identical timestamps).  Default
        implementation drains ``_pedal_subtask`` (initialised in
        ``open()``).
        """
        if self._pedal_subtask.is_set():
            self._pedal_subtask.clear()
            return True
        return False

    def poll(self, robot_controller: "RobotController") -> bool:
        """Return True on a session-level trigger event (leader-arm button,
        etc.).  Used to start/stop episodes on interfaces with hardware
        buttons.  Default: never triggers."""
        return False

    def poll_pedal_trigger(self) -> bool:
        """Return True if the pedal was pressed outside an active recording.

        Used by calibration pose recording as a hands-free trigger.
        Clear-on-read; the latch is initialised in ``open()``.
        """
        if self._pedal_trigger.is_set():
            self._pedal_trigger.clear()
            return True
        return False

    def drain_pedal_events(self, robot_controller: "RobotController") -> None:
        """Discard any latched pedal events so they don't bleed across phases.

        ``threading.Event``-backed pedal latches stay set until polled;
        without an explicit drain at episode boundaries a stray press
        queued during a previous wait would auto-mark the next episode.
        """
        self.poll(robot_controller)
        self.poll_subtask(robot_controller)
        self.poll_pedal_trigger()

    @property
    def uses_leaders(self) -> bool:
        """True if this mode requires leader arms to be initialised."""
        return False

    @property
    def waits_for_button_start(self) -> bool:
        """True if recording should start on a leader-arm button press.

        False means keyboard / Enter key is used instead.
        """
        return self.uses_leaders
