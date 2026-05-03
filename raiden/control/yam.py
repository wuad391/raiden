"""Leader-follower teleoperation using YAM teaching handles."""

from raiden.control.base import TeleopInterface


class YAMInterface(TeleopInterface):
    """Follower arms mirror leader arms in real time."""

    @property
    def name(self) -> str:
        return "leader"

    @property
    def uses_leaders(self) -> bool:
        return True

    # ------------------------------------------------------------------
    # Episode-level lifecycle (footpedal handled by TeleopInterface base)
    # ------------------------------------------------------------------

    def setup(self, robot_controller) -> None:
        pass  # leaders are already configured by setup_for_teleop_recording()

    def start(self, robot_controller) -> None:
        robot_controller.start_teleoperation()

    def stop(self, robot_controller) -> None:
        robot_controller.stop_teleoperation()

    def poll(self, robot_controller) -> bool:
        # Leader-arm button is the start/stop trigger for YAM.
        return robot_controller.check_button_press() is not None

    @property
    def banner(self) -> str:
        return (
            "\n" + "=" * 60 + "\n"
            "  BIMANUAL TELEOPERATION ACTIVE\n" + "=" * 60 + "\n\n"
            "  Press the button on any leader arm to return ALL arms to home\n"
            "  Press Ctrl+C for EMERGENCY STOP (stops all motion immediately)\n\n"
            + "=" * 60
            + "\n"
        )
