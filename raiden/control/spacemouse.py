"""SpaceMouse Cartesian velocity teleoperation."""

from raiden.control.base import TeleopInterface


class SpaceMouseInterface(TeleopInterface):
    """EE velocity control via SpaceMouse puck(s)."""

    def __init__(
        self,
        path_r: str = "/dev/hidraw4",
        path_l: str = "/dev/hidraw5",
        vel_scale: float = 0.07,
        rot_scale: float = 0.8,
        invert_rotation: bool = False,
    ):
        self._path_r = path_r
        self._path_l = path_l
        self._vel_scale = vel_scale
        self._rot_scale = rot_scale
        self._invert_rotation = invert_rotation

    @property
    def name(self) -> str:
        return "spacemouse"

    # ------------------------------------------------------------------
    # Episode-level lifecycle (footpedal handled by TeleopInterface base)
    # ------------------------------------------------------------------

    def setup(self, robot_controller) -> None:
        robot_controller.warmup_spacemouse_ik()
        robot_controller.attach_spacemice(self._path_r, self._path_l)

    def start(self, robot_controller) -> None:
        robot_controller.start_spacemouse_teleop(
            vel_scale=self._vel_scale,
            rot_scale=self._rot_scale,
            invert_rotation=self._invert_rotation,
        )

    def stop(self, robot_controller) -> None:
        robot_controller.stop_spacemouse_teleop()

    @property
    def banner(self) -> str:
        return (
            "\n" + "=" * 60 + "\n"
            "  SPACEMOUSE TELEOPERATION ACTIVE\n" + "=" * 60 + "\n\n"
            "  Push/pull/tilt puck to move EE, rock/twist to rotate\n"
            "  Press Ctrl+C to stop\n\n" + "=" * 60 + "\n"
        )
