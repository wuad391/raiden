"""Event-marker schema + clock-fallback semantics — BUG-4 surface area.

Covers:
  - `add_event_marker()` is a no-op when not recording.
  - Normal path writes `{"t": <ns>, "elapsed_s": <float>, "clock": "camera"}`.
  - The narrowed except `(RuntimeError, OSError, ValueError)` produces
    `clock="wallclock_fallback"` and a `t` near `time.time_ns()`.
  - Unexpected exceptions propagate (not silently swallowed).
  - The `event_markers` list lands in the persisted `metadata.json`.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Optional

import pytest

from raiden.recorder import (
    _CLOCK_CAMERA,
    _CLOCK_FALLBACK,
    DemonstrationRecorder,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class FakeCamera:
    """Minimal Camera stand-in for `add_event_marker`'s clock read."""

    def __init__(
        self,
        ts_ns: Optional[int] = None,
        raises: Optional[BaseException] = None,
        name: str = "scene_camera",
    ):
        self.name = name
        self._ts_ns = ts_ns
        self._raises = raises
        self._clock_offset_ns = None

    def get_current_timestamp_ns(self) -> int:
        if self._raises is not None:
            raise self._raises
        return self._ts_ns


def _make_recorder(
    camera: FakeCamera, recording_dir: Path, *, recording: bool = True
) -> DemonstrationRecorder:
    """Build a DemonstrationRecorder bypassing `__init__`'s heavy deps."""
    rec: DemonstrationRecorder = DemonstrationRecorder.__new__(DemonstrationRecorder)
    rec.cameras = [camera]
    rec.is_recording = recording
    rec._start_time = time.monotonic()
    rec._event_markers = []
    rec.recording_dir = recording_dir
    rec.task_name = "test_task"
    rec.task_instruction = "test instruction"
    rec.interface = SimpleNamespace(name="spacemouse")
    rec._robot_frames = []
    rec._camera_start_times_ns = {}
    rec.audio_recorder = None
    return rec


# ---------------------------------------------------------------------------
# add_event_marker behaviour
# ---------------------------------------------------------------------------


def test_add_event_marker_no_op_when_not_recording(tmp_path):
    cam = FakeCamera(ts_ns=1)
    rec = _make_recorder(cam, tmp_path, recording=False)
    rec.add_event_marker()
    assert rec._event_markers == []


def test_add_event_marker_normal_path_uses_camera_clock(tmp_path):
    cam = FakeCamera(ts_ns=1_700_000_000_123_456_789)
    rec = _make_recorder(cam, tmp_path)
    rec.add_event_marker()
    assert len(rec._event_markers) == 1
    marker = rec._event_markers[0]
    assert marker["t"] == 1_700_000_000_123_456_789
    assert marker["clock"] == _CLOCK_CAMERA
    assert isinstance(marker["elapsed_s"], float)


@pytest.mark.parametrize(
    "exc", [RuntimeError("zed sdk"), OSError("io"), ValueError("nan")]
)
def test_add_event_marker_falls_back_on_narrow_excs(tmp_path, exc, capsys):
    cam = FakeCamera(raises=exc)
    rec = _make_recorder(cam, tmp_path)
    before = time.time_ns()
    rec.add_event_marker()
    after = time.time_ns()
    assert len(rec._event_markers) == 1
    marker = rec._event_markers[0]
    assert marker["clock"] == _CLOCK_FALLBACK
    assert before <= marker["t"] <= after
    err_text = capsys.readouterr().out
    assert "Event marker camera-clock read failed" in err_text


def test_add_event_marker_unexpected_exception_propagates(tmp_path):
    """KeyError is not in the narrowed except — must not be swallowed."""
    cam = FakeCamera(raises=KeyError("unexpected"))
    rec = _make_recorder(cam, tmp_path)
    with pytest.raises(KeyError):
        rec.add_event_marker()
    assert rec._event_markers == []


def test_add_event_marker_records_multiple_in_order(tmp_path):
    cam = FakeCamera(ts_ns=2_000_000_000)
    rec = _make_recorder(cam, tmp_path)
    rec.add_event_marker()
    cam._ts_ns = 3_000_000_000
    rec.add_event_marker()
    assert [m["t"] for m in rec._event_markers] == [2_000_000_000, 3_000_000_000]
    assert all(m["clock"] == _CLOCK_CAMERA for m in rec._event_markers)


# ---------------------------------------------------------------------------
# metadata.json persistence
# ---------------------------------------------------------------------------


def test_metadata_persists_event_markers_with_clock_field(tmp_path):
    cam = FakeCamera(ts_ns=1_700_000_000_000_000_000)
    rec = _make_recorder(cam, tmp_path)
    rec.add_event_marker()
    cam._raises = RuntimeError("simulated transient")
    rec.add_event_marker()

    rec._save_metadata(duration=1.0, complete=True)

    meta = json.loads((tmp_path / "metadata.json").read_text())
    assert "event_markers" in meta
    assert len(meta["event_markers"]) == 2
    for marker in meta["event_markers"]:
        assert set(marker.keys()) == {"t", "elapsed_s", "clock"}
    assert meta["event_markers"][0]["clock"] == _CLOCK_CAMERA
    assert meta["event_markers"][1]["clock"] == _CLOCK_FALLBACK


def test_metadata_omits_event_markers_key_when_none_recorded(tmp_path):
    cam = FakeCamera(ts_ns=1)
    rec = _make_recorder(cam, tmp_path)
    rec._save_metadata(duration=0.5, complete=True)
    meta = json.loads((tmp_path / "metadata.json").read_text())
    assert "event_markers" not in meta
