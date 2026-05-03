"""Shared fixtures for the foot-pedal test suite.

Stubs heavy third-party deps at import time so the modules under test
load in a thin venv without raiden's full ML/robot stack.
"""

from __future__ import annotations

import sys
import types
from typing import Callable, Dict, List, Optional
from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# sys.modules stubs (must run before any raiden.* import)
# ---------------------------------------------------------------------------

_STUB_MODULES = [
    "boto3",
    "iterfzf",
    "pysondb",
    "jax",
    "jax.numpy",
    "jax.typing",
    "jaxlie",
    "pyroki",
    "pyspacemouse",
    "yourdfpy",
    "pyrealsense2",
    "scipy",
    "scipy.spatial",
    "scipy.spatial.transform",
    "i2rt",
    "i2rt.motor_drivers",
    "i2rt.motor_drivers.can_interface",
    "i2rt.robots",
    "i2rt.robots.get_robot",
    "i2rt.robots.motor_chain_robot",
    "i2rt.robots.robot",
    "i2rt.robots.utils",
    "i2rt.robots.kinematics",
]


def _install_stub(name: str) -> types.ModuleType:
    parts = name.split(".")
    for i in range(len(parts)):
        sub = ".".join(parts[: i + 1])
        if sub not in sys.modules:
            mod = types.ModuleType(sub)
            mod.__path__ = []  # type: ignore[attr-defined]
            sys.modules[sub] = mod
        else:
            mod = sys.modules[sub]
        if i > 0:
            parent = sys.modules[".".join(parts[:i])]
            setattr(parent, parts[i], mod)
    leaf = sys.modules[name]
    # Make every attribute access on the leaf return a MagicMock-ish placeholder
    # so `from <name> import Anything` works.
    leaf.__getattr__ = lambda _attr, _m=leaf: MagicMock()  # type: ignore[attr-defined]
    return leaf


for _name in _STUB_MODULES:
    _install_stub(_name)


def _make_evdev_stub() -> types.ModuleType:
    """Stub `evdev` so footpedal.py imports cleanly without the kernel HID dep."""
    mod = types.ModuleType("evdev")
    ec = types.SimpleNamespace(
        EV_KEY=1,
        KEY_A=30,
        KEY_B=48,
        KEY_C=46,
        KEY_SPACE=57,
        KEY_LEFTSHIFT=42,
        KEY_RIGHTSHIFT=54,
        KEY_EQUAL=13,
        KEY_MINUS=12,
    )
    mod.ecodes = ec
    mod.InputDevice = MagicMock()
    sys.modules["evdev"] = mod
    sys.modules["evdev.ecodes"] = ec  # type: ignore[assignment]
    return mod


_make_evdev_stub()


# i2rt symbols imported by name from the stub modules.
# CanInterface needs to be a *real* class because raiden.robot.controller
# does `CanInterface.__init__ = _patched_can_interface_init` at import time,
# which MagicMock rejects.
class _PlaceholderCanInterface:
    def __init__(self, *a, **kw):
        pass


sys.modules["i2rt.motor_drivers.can_interface"].CanInterface = _PlaceholderCanInterface
sys.modules["i2rt.robots.utils"].ARM_YAM_XML_PATH = "/dev/null/yam.xml"
sys.modules["i2rt.robots.utils"].GripperType = MagicMock()


# ---------------------------------------------------------------------------
# FakeFootPedal — captures on_press, fires synchronously
# ---------------------------------------------------------------------------


class FakeFootPedal:
    """Drop-in replacement for `raiden.robot.footpedal.FootPedal`.

    Captures the callback registered via `on_press(...)` and fires it
    synchronously via `press(code)` on the test thread. No background
    thread, no evdev, no kernel device.
    """

    def __init__(self) -> None:
        self._callbacks: List[Callable[[int], None]] = []
        self.started = False
        self.closed = False

    def on_press(self, callback: Callable[[int], None]) -> None:
        self._callbacks.append(callback)

    def start(self) -> None:
        self.started = True

    def close(self) -> None:
        self.closed = True

    def press(self, code: int) -> None:
        """Simulate a key-down event from the foot pedal."""
        for cb in list(self._callbacks):
            cb(code)


# ---------------------------------------------------------------------------
# FakeRobotController — minimal RobotController stand-in
# ---------------------------------------------------------------------------


class FakeRobotController:
    """Minimal RobotController stand-in for pedal/recorder tests."""

    def __init__(self) -> None:
        self.session_estop_requested = False
        self._button_press: Optional[str] = None

    def enable_estop(self) -> None:
        pass

    def shutdown(self) -> None:
        pass

    def check_button_press(self) -> Optional[str]:
        return self._button_press

    def set_button_press(self, value: Optional[str]) -> None:
        self._button_press = value


# ---------------------------------------------------------------------------
# Pytest fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_pedal() -> FakeFootPedal:
    return FakeFootPedal()


@pytest.fixture
def robot_controller() -> FakeRobotController:
    return FakeRobotController()


@pytest.fixture
def patched_try_open(fake_pedal: FakeFootPedal, monkeypatch: pytest.MonkeyPatch):
    """Monkeypatch `try_open_footpedal` where TeleopInterface imports it."""
    from raiden.control import base as control_base

    monkeypatch.setattr(control_base, "try_open_footpedal", lambda *a, **kw: fake_pedal)
    return fake_pedal


@pytest.fixture(params=["spacemouse", "yam"])
def any_interface(request, patched_try_open):
    """Parametrised over both production interfaces."""
    if request.param == "spacemouse":
        from raiden.control.spacemouse import SpaceMouseInterface

        iface = SpaceMouseInterface()
    else:
        from raiden.control.yam import YAMInterface

        iface = YAMInterface()
    iface.open()
    yield iface
    iface.close()


# ---------------------------------------------------------------------------
# Pedal codes (re-exported for tests so they don't depend on evdev)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def pedal_codes() -> Dict[str, int]:
    from raiden.robot.footpedal import PEDAL_LEFT, PEDAL_MIDDLE, PEDAL_RIGHT

    return {"left": PEDAL_LEFT, "middle": PEDAL_MIDDLE, "right": PEDAL_RIGHT}
