"""AudioRecorder lifecycle, segmentation, and fail-soft semantics.

Pins:
  - Without PyAudio installed, ``start_session`` is a no-op (recorder
    `enabled=False`); episode hooks do nothing; recording continues.
  - With a fake PyAudio injected, the daemon thread opens the stream at
    ``start_episode`` (not on first press), captures continuously,
    discards pre-first-press warm-up noise, writes ``audio_full.wav``
    spanning first-press → end-of-episode, and ALSO writes one WAV per
    pedal-press boundary.
  - ``audio_full.start_t_ns`` and each segment's ``boundary_t_ns`` come
    from the injected ``capture_clock`` and propagate the ``clock``
    discriminator into both the sidecars and the drained dict.
"""

from __future__ import annotations

import json
import threading
import time
from typing import List, Tuple
from unittest.mock import MagicMock

import pytest

import raiden.audio as audio_mod
from raiden.audio import AudioRecorder, _CLOCK_CAMERA, _CLOCK_FALLBACK


# ---------------------------------------------------------------------------
# Fake PyAudio
# ---------------------------------------------------------------------------


class FakeStream:
    """Minimal PyAudio Stream stand-in. The test pumps frames manually."""

    def __init__(self, callback) -> None:
        self.callback = callback
        self.closed = False

    def push(self, frame_bytes: bytes) -> None:
        # PyAudio callback signature: (in_data, frame_count, time_info, status).
        self.callback(frame_bytes, len(frame_bytes) // 2, None, 0)

    def stop_stream(self) -> None:
        pass

    def close(self) -> None:
        self.closed = True


class FakePyAudio:
    """Stand-in for `pyaudio.PyAudio`. Returns a single FakeStream per open()."""

    def __init__(self) -> None:
        self.streams: List[FakeStream] = []
        self.terminated = False

    def get_default_input_device_info(self) -> dict:
        return {"name": "fake_default", "index": 0}

    def get_device_info_by_index(self, idx: int) -> dict:
        return {"name": f"fake_dev_{idx}", "index": idx}

    def open(self, *, stream_callback, **_kw) -> FakeStream:
        s = FakeStream(stream_callback)
        self.streams.append(s)
        return s

    def terminate(self) -> None:
        self.terminated = True


@pytest.fixture
def fake_pyaudio(monkeypatch):
    """Install a fake PyAudio so AudioRecorder thinks it has a device."""
    fake_pa_mod = MagicMock()
    fake_pa_mod.paInt16 = 8
    fake_pa_mod.paContinue = 0
    fake_instance = FakePyAudio()
    fake_pa_mod.PyAudio = lambda: fake_instance

    monkeypatch.setattr(audio_mod, "pyaudio", fake_pa_mod, raising=False)
    monkeypatch.setattr(audio_mod, "_PYAUDIO_AVAILABLE", True, raising=False)
    monkeypatch.setattr(audio_mod, "_PYAUDIO_FORMAT_INT16", fake_pa_mod.paInt16,
                        raising=False)
    monkeypatch.setattr(audio_mod, "_PYAUDIO_PA_CONTINUE", fake_pa_mod.paContinue,
                        raising=False)
    return fake_instance


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _PressQueue:
    """Drives `poll_press` from the test thread. Each emit() returns True
    on the next call only. Thread-safe."""

    def __init__(self) -> None:
        self._pending = 0
        self._lock = threading.Lock()

    def emit(self) -> None:
        with self._lock:
            self._pending += 1

    def __call__(self) -> bool:
        with self._lock:
            if self._pending > 0:
                self._pending -= 1
                return True
            return False


class _ClockSequence:
    """Returns successive ts_ns values so each capture has a distinct clock."""

    def __init__(self, start_ns: int = 1_700_000_000_000_000_000,
                 step_ns: int = 1_000_000) -> None:
        self._t = start_ns
        self._step = step_ns
        self._lock = threading.Lock()
        self.label = _CLOCK_CAMERA

    def __call__(self) -> Tuple[int, str]:
        with self._lock:
            t = self._t
            self._t += self._step
            return t, self.label


def _wait_for(predicate, timeout: float, poll: float = 0.05) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(poll)
    raise AssertionError(f"timeout waiting for {predicate}")


# ---------------------------------------------------------------------------
# Fail-soft path: PyAudio missing
# ---------------------------------------------------------------------------


def test_recorder_disabled_when_pyaudio_unavailable(monkeypatch, tmp_path,
                                                    capsys):
    """Without PyAudio the recorder is a no-op; recording continues."""
    monkeypatch.setattr(audio_mod, "_PYAUDIO_AVAILABLE", False, raising=False)
    rec = AudioRecorder(poll_press=lambda: False,
                        capture_clock=lambda: (0, _CLOCK_CAMERA))
    rec.start_session()
    assert rec.enabled is False
    rec.start_episode(tmp_path)
    rec.stop_episode()
    assert rec.wait_until_idle(timeout=0.1) is True
    drained = rec.drain()
    assert drained == {"audio_full": None, "audio_segments": []}
    rec.stop_session()
    out = capsys.readouterr().out
    assert "audio recording disabled" in out.lower() or "PyAudio not installed" in out


def test_recorder_disabled_when_no_input_device(monkeypatch, tmp_path, capsys):
    """If the default input device can't be opened, fail-soft to disabled."""
    monkeypatch.setattr(audio_mod, "_PYAUDIO_AVAILABLE", True, raising=False)
    fake_mod = MagicMock()
    fake_mod.paInt16 = 8
    fake_mod.paContinue = 0

    class _NoDevicePyAudio:
        def get_default_input_device_info(self):
            raise OSError("No Default Input Device Available")

        def terminate(self):
            pass

    fake_mod.PyAudio = _NoDevicePyAudio
    monkeypatch.setattr(audio_mod, "pyaudio", fake_mod, raising=False)
    monkeypatch.setattr(audio_mod, "_PYAUDIO_FORMAT_INT16", fake_mod.paInt16,
                        raising=False)
    monkeypatch.setattr(audio_mod, "_PYAUDIO_PA_CONTINUE", fake_mod.paContinue,
                        raising=False)

    rec = AudioRecorder(poll_press=lambda: False,
                        capture_clock=lambda: (0, _CLOCK_CAMERA))
    rec.start_session()
    assert rec.enabled is False
    out = capsys.readouterr().out
    assert "no microphone available" in out.lower()


# ---------------------------------------------------------------------------
# Happy path with fake PyAudio
# ---------------------------------------------------------------------------


def test_no_press_writes_nothing(fake_pyaudio, tmp_path):
    """Episode with no pedal press = no audio files at all.

    Pre-first-press frames are warm-up noise and discarded.  Without a
    first press there's no anchor, so neither segments nor audio_full
    land on disk.
    """
    rec = AudioRecorder(poll_press=lambda: False,
                        capture_clock=lambda: (1_700_000_000, _CLOCK_CAMERA))
    rec.start_session()
    rec.start_episode(tmp_path)
    _wait_for(lambda: len(fake_pyaudio.streams) == 1, timeout=2.0)
    fake_pyaudio.streams[0].push(b"\x00" * 2048)
    rec.stop_episode()
    assert rec.wait_until_idle(timeout=3.0) is True

    assert not (tmp_path / "audio").exists()
    drained = rec.drain()
    assert drained == {"audio_full": None, "audio_segments": []}
    rec.stop_session()


def test_two_presses_yield_audio_full_plus_two_segments(fake_pyaudio, tmp_path):
    """Two presses produce two segments AND audio_full.wav anchored at
    the first press.  Pre-first-press warm-up noise is discarded."""
    presses = _PressQueue()
    clock = _ClockSequence()
    rec = AudioRecorder(poll_press=presses, capture_clock=clock)
    rec.start_session()
    rec.start_episode(tmp_path)
    _wait_for(lambda: len(fake_pyaudio.streams) == 1, timeout=2.0)
    stream = fake_pyaudio.streams[0]

    # Pre-first-press warm-up noise — must be discarded, not written.
    stream.push(b"\x01\x00" * 1024)

    presses.emit()
    time.sleep(0.2)
    stream.push(b"\x02\x00" * 1024)
    stream.push(b"\x03\x00" * 1024)

    presses.emit()
    time.sleep(0.2)
    stream.push(b"\x04\x00" * 1024)
    stream.push(b"\x05\x00" * 1024)

    rec.stop_episode()
    assert rec.wait_until_idle(timeout=3.0) is True

    audio_dir = tmp_path / "audio"
    wav_files = sorted(audio_dir.glob("*.wav"))
    json_files = sorted(audio_dir.glob("*.json"))
    # audio_full + 2 segments.
    assert len(wav_files) == 3
    assert (audio_dir / "audio_full.wav").exists()
    seg_jsons = sorted(p for p in json_files if p.stem != "audio_full")
    assert len(seg_jsons) == 2

    for sc_path in seg_jsons:
        sc = json.loads(sc_path.read_text())
        assert set(sc.keys()) >= {
            "audio_file", "segment_id", "boundary_t_ns",
            "duration_s", "clock", "sample_rate", "channels",
        }
        assert sc["clock"] == _CLOCK_CAMERA

    full_sc = json.loads((audio_dir / "audio_full.json").read_text())
    assert set(full_sc.keys()) >= {
        "audio_file", "start_t_ns", "duration_s", "clock",
        "sample_rate", "channels",
    }
    assert full_sc["clock"] == _CLOCK_CAMERA

    drained = rec.drain()
    assert drained["audio_full"] is not None
    assert len(drained["audio_segments"]) == 2
    assert all(s["clock"] == _CLOCK_CAMERA for s in drained["audio_segments"])

    boundaries = [s["boundary_t_ns"] for s in drained["audio_segments"]]
    assert boundaries == sorted(boundaries) and len(set(boundaries)) == len(boundaries)
    # audio_full anchors AT the first press (warm-up discarded).
    assert drained["audio_full"]["start_t_ns"] == boundaries[0]
    # audio_full duration ≈ sum of segment durations (sample-aligned concat).
    # Both are round(_, 3) of float seconds, so allow ~2 ms of rounding slack.
    seg_total = sum(s["duration_s"] for s in drained["audio_segments"])
    assert abs(drained["audio_full"]["duration_s"] - seg_total) < 0.01
    rec.stop_session()


def test_segment_clock_label_propagated_on_fallback(fake_pyaudio, tmp_path):
    """When capture_clock returns wallclock_fallback, both the audio_full
    sidecar and per-segment sidecars surface that label."""
    presses = _PressQueue()
    fallback_clock = _ClockSequence()
    fallback_clock.label = _CLOCK_FALLBACK

    rec = AudioRecorder(poll_press=presses, capture_clock=fallback_clock)
    rec.start_session()
    rec.start_episode(tmp_path)
    _wait_for(lambda: len(fake_pyaudio.streams) == 1, timeout=2.0)
    fake_pyaudio.streams[0].push(b"\x00" * 2048)
    presses.emit()
    time.sleep(0.2)
    fake_pyaudio.streams[0].push(b"\x00" * 2048)
    rec.stop_episode()
    assert rec.wait_until_idle(timeout=3.0) is True

    drained = rec.drain()
    assert drained["audio_full"]["clock"] == _CLOCK_FALLBACK
    assert len(drained["audio_segments"]) == 1
    assert drained["audio_segments"][0]["clock"] == _CLOCK_FALLBACK
    rec.stop_session()


def test_drain_clears_after_read(fake_pyaudio, tmp_path):
    """Two consecutive drains: second has audio_full=None and empty segments."""
    presses = _PressQueue()
    rec = AudioRecorder(poll_press=presses,
                        capture_clock=lambda: (0, _CLOCK_CAMERA))
    rec.start_session()
    rec.start_episode(tmp_path)
    _wait_for(lambda: len(fake_pyaudio.streams) == 1, timeout=2.0)
    fake_pyaudio.streams[0].push(b"\x00" * 2048)
    presses.emit()
    time.sleep(0.2)
    fake_pyaudio.streams[0].push(b"\x00" * 2048)
    rec.stop_episode()
    rec.wait_until_idle(timeout=3.0)

    first = rec.drain()
    second = rec.drain()
    assert first["audio_full"] is not None
    assert len(first["audio_segments"]) >= 1
    assert second == {"audio_full": None, "audio_segments": []}
    rec.stop_session()


def test_stop_session_terminates_pyaudio(fake_pyaudio, tmp_path):
    rec = AudioRecorder(poll_press=lambda: False,
                        capture_clock=lambda: (0, _CLOCK_CAMERA))
    rec.start_session()
    rec.stop_session()
    assert fake_pyaudio.terminated is True
