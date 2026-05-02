"""AudioRecorder lifecycle, segmentation, and fail-soft semantics.

Pins:
  - Without PyAudio installed, ``start_session`` is a no-op (recorder
    `enabled=False`); episode hooks do nothing; recording continues.
  - With a fake PyAudio injected, the daemon thread opens the stream at
    ``start_episode`` (not on first press), captures continuously,
    discards pre-first-press warm-up noise, writes ``audio_full.wav``
    spanning first-press → end-of-episode, and writes one WAV per
    pedal-press boundary.
  - Boundaries are pushed via ``mark_boundary(ts_ns, clock)`` from the
    recorder thread (not polled / not separately timestamped on the
    audio thread), so each segment's boundary timestamp matches the
    matching ``event_markers`` entry bit-for-bit.
"""

from __future__ import annotations

import json
import time
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
        self.streams: list = []
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
    monkeypatch.setattr(
        audio_mod, "_PYAUDIO_FORMAT_INT16", fake_pa_mod.paInt16, raising=False
    )
    monkeypatch.setattr(
        audio_mod, "_PYAUDIO_PA_CONTINUE", fake_pa_mod.paContinue, raising=False
    )
    return fake_instance


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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


def test_recorder_disabled_when_pyaudio_unavailable(monkeypatch, tmp_path, capsys):
    """Without PyAudio the recorder is a no-op; recording continues."""
    monkeypatch.setattr(audio_mod, "_PYAUDIO_AVAILABLE", False, raising=False)
    rec = AudioRecorder()
    rec.start_session()
    assert rec.enabled is False
    rec.start_episode(tmp_path)
    rec.mark_boundary(1_700_000_000, _CLOCK_CAMERA)  # ignored
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
    monkeypatch.setattr(
        audio_mod, "_PYAUDIO_FORMAT_INT16", fake_mod.paInt16, raising=False
    )
    monkeypatch.setattr(
        audio_mod, "_PYAUDIO_PA_CONTINUE", fake_mod.paContinue, raising=False
    )

    rec = AudioRecorder()
    rec.start_session()
    assert rec.enabled is False
    out = capsys.readouterr().out
    assert "no microphone available" in out.lower()


# ---------------------------------------------------------------------------
# Stream-open failure does not spin
# ---------------------------------------------------------------------------


def test_open_failure_clears_episode_running(monkeypatch, tmp_path, capsys):
    """If pa.open() raises mid-session, _episode_running is cleared so the
    daemon doesn't immediately re-enter and spam logs."""
    fake_pa_mod = MagicMock()
    fake_pa_mod.paInt16 = 8
    fake_pa_mod.paContinue = 0

    class _OpenFailsPyAudio:
        def get_default_input_device_info(self):
            return {"name": "fake", "index": 0}

        def open(self, **_kw):
            raise OSError("device busy")

        def terminate(self):
            pass

    fake_pa_mod.PyAudio = _OpenFailsPyAudio
    monkeypatch.setattr(audio_mod, "pyaudio", fake_pa_mod, raising=False)
    monkeypatch.setattr(audio_mod, "_PYAUDIO_AVAILABLE", True, raising=False)
    monkeypatch.setattr(
        audio_mod, "_PYAUDIO_FORMAT_INT16", fake_pa_mod.paInt16, raising=False
    )
    monkeypatch.setattr(
        audio_mod, "_PYAUDIO_PA_CONTINUE", fake_pa_mod.paContinue, raising=False
    )

    rec = AudioRecorder()
    rec.start_session()
    assert rec.enabled is True
    rec.start_episode(tmp_path)
    # Give the daemon time to attempt + fail the open.
    time.sleep(0.3)
    # _episode_running should have been cleared by the failure path so
    # the daemon went back to sleep instead of re-entering.
    assert not rec._episode_running.is_set()
    rec.stop_session()
    out = capsys.readouterr().out
    # Exactly one failure log per failed open.
    assert out.count("AudioRecorder.open() failed") == 1


# ---------------------------------------------------------------------------
# Happy path with fake PyAudio
# ---------------------------------------------------------------------------


def test_no_press_writes_nothing(fake_pyaudio, tmp_path):
    """Episode with no pedal press = no audio files at all.

    Pre-first-press frames are warm-up noise and discarded.  Without a
    first press there's no anchor, so neither segments nor audio_full
    land on disk.
    """
    rec = AudioRecorder()
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
    """Two mark_boundary calls produce two segments AND audio_full.wav
    anchored at the first press.  Pre-first-press warm-up noise is
    discarded.  Boundary timestamps come from mark_boundary verbatim."""
    ts0, ts1 = 1_700_000_000_000_000_000, 1_700_000_000_000_001_000
    rec = AudioRecorder()
    rec.start_session()
    rec.start_episode(tmp_path)
    _wait_for(lambda: len(fake_pyaudio.streams) == 1, timeout=2.0)
    stream = fake_pyaudio.streams[0]

    # Pre-first-press warm-up noise — must be discarded, not written.
    stream.push(b"\x01\x00" * 1024)

    rec.mark_boundary(ts0, _CLOCK_CAMERA)
    time.sleep(0.2)
    stream.push(b"\x02\x00" * 1024)
    stream.push(b"\x03\x00" * 1024)

    rec.mark_boundary(ts1, _CLOCK_CAMERA)
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
            "audio_file",
            "segment_id",
            "boundary_t_ns",
            "duration_s",
            "clock",
            "sample_rate",
            "channels",
        }
        assert sc["clock"] == _CLOCK_CAMERA

    full_sc = json.loads((audio_dir / "audio_full.json").read_text())
    assert set(full_sc.keys()) >= {
        "audio_file",
        "start_t_ns",
        "duration_s",
        "clock",
        "sample_rate",
        "channels",
    }
    assert full_sc["clock"] == _CLOCK_CAMERA

    drained = rec.drain()
    assert drained["audio_full"] is not None
    assert len(drained["audio_segments"]) == 2

    boundaries = [s["boundary_t_ns"] for s in drained["audio_segments"]]
    # Boundary timestamps are EXACTLY what mark_boundary received — no
    # second clock read on the audio thread.
    assert boundaries == [ts0, ts1]
    # audio_full anchors at the first press (warm-up discarded).
    assert drained["audio_full"]["start_t_ns"] == ts0
    # audio_full duration ≈ sum of segment durations (sample-aligned concat).
    seg_total = sum(s["duration_s"] for s in drained["audio_segments"])
    assert abs(drained["audio_full"]["duration_s"] - seg_total) < 0.01
    rec.stop_session()


def test_segment_clock_label_propagated_on_fallback(fake_pyaudio, tmp_path):
    """When mark_boundary is called with wallclock_fallback, both the
    audio_full sidecar and per-segment sidecars surface that label."""
    rec = AudioRecorder()
    rec.start_session()
    rec.start_episode(tmp_path)
    _wait_for(lambda: len(fake_pyaudio.streams) == 1, timeout=2.0)
    fake_pyaudio.streams[0].push(b"\x00" * 2048)
    rec.mark_boundary(1_700_000_000, _CLOCK_FALLBACK)
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
    rec = AudioRecorder()
    rec.start_session()
    rec.start_episode(tmp_path)
    _wait_for(lambda: len(fake_pyaudio.streams) == 1, timeout=2.0)
    fake_pyaudio.streams[0].push(b"\x00" * 2048)
    rec.mark_boundary(1_700_000_000, _CLOCK_CAMERA)
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


def test_mark_boundary_ignored_outside_episode(fake_pyaudio, tmp_path):
    """mark_boundary called when no episode is active is a silent no-op."""
    rec = AudioRecorder()
    rec.start_session()
    # No start_episode → mark_boundary should be ignored.
    rec.mark_boundary(1_700_000_000, _CLOCK_CAMERA)
    assert len(rec._pending_boundaries) == 0
    rec.stop_session()


def test_stop_session_terminates_pyaudio(fake_pyaudio, tmp_path):
    rec = AudioRecorder()
    rec.start_session()
    rec.stop_session()
    assert fake_pyaudio.terminated is True
