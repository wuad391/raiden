"""Microphone-based subtask narration aligned with event markers.

Pattern (mirrors the validated `xarm_audio_pedal/record_audio.py`):
  - First audio-pedal press during an active recording starts a continuous
    PyAudio input stream.
  - Each subsequent press marks a segment boundary in the running stream.
  - On episode stop, the stream is closed and one WAV is written per
    segment under ``<recording_dir>/audio/`` along with a JSON sidecar.

Boundary timestamps share the camera/robot clock used by ``event_markers``
(via the ``capture_clock`` callable injected by ``DemonstrationRecorder``),
so audio narration aligns with `robot_data.npz` and converted camera frames
without a post-hoc shim.
"""

from __future__ import annotations

import json
import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Deque, Dict, List, Optional, Tuple

# PyAudio is an optional extra. Keep the import soft so a non-audio install
# of raiden imports `raiden.audio` without crashing — `AudioRecorder.start_session`
# raises a clear install-message if audio was actually requested.
try:
    import pyaudio  # type: ignore[import-not-found]

    _PYAUDIO_AVAILABLE = True
    _PYAUDIO_FORMAT_INT16 = pyaudio.paInt16
    _PYAUDIO_PA_CONTINUE = pyaudio.paContinue
except ImportError:  # pragma: no cover — exercised only on install-without-extra
    pyaudio = None  # type: ignore[assignment]
    _PYAUDIO_AVAILABLE = False
    _PYAUDIO_FORMAT_INT16 = None
    _PYAUDIO_PA_CONTINUE = None


from raiden._clock import CLOCK_CAMERA as _CLOCK_CAMERA  # noqa: F401  (re-exported for tests)
from raiden._clock import CLOCK_FALLBACK as _CLOCK_FALLBACK  # noqa: F401
from raiden._warn import warn as _warn

# Audio-format constants (matches the reference repo's `audio_common.py`).
SAMPLE_RATE = 48000
CHANNELS = 1
SAMPLE_WIDTH = 2  # bytes per sample for int16
CHUNK = 1024


class AudioRecorder:
    """Continuous microphone recorder driven by ``mark_boundary`` calls.

    One instance per session. Episode-scoped state (segments, frames,
    recording_dir) is rebuilt at the start of each episode.

    Boundaries are pushed in by the recorder thread via ``mark_boundary``
    (which carries the same ``(t_ns, clock)`` value used for the matching
    ``event_markers`` entry).  This guarantees that ``event_markers[i].t``
    and ``audio_segments[i].boundary_t_ns`` are bit-identical — there is
    no second clock read on the audio thread.

    Lifecycle::

        ar = AudioRecorder()
        ar.start_session()                  # spins the daemon thread
        ar.start_episode(rec_dir)           # at the top of each episode
        ar.mark_boundary(ts_ns, clock)      # called by the recorder loop on each press
        ar.stop_episode()                   # signals end of episode
        ar.wait_until_idle()                # block until WAVs are flushed
        audio = ar.drain()                  # {"audio_full": ..., "audio_segments": [...]}
        ar.stop_session()                   # at session end

    The recorder is fail-soft: if PyAudio is missing or the input device
    can't be opened, ``start_session`` prints a yellow warning and the
    recorder becomes a no-op (boundaries are ignored, no WAVs written,
    the rest of recording continues). Callers don't need to special-case
    this.
    """

    def __init__(self, device_index: Optional[int] = None) -> None:
        self._device_index = device_index

        self._pa: Optional["pyaudio.PyAudio"] = None
        self._enabled = False  # Becomes True iff start_session opens PyAudio cleanly.

        # Session-level
        self._session_running = False
        self._thread: Optional[threading.Thread] = None

        # Episode-level (re-initialised each episode)
        self._episode_dir: Optional[Path] = None
        self._episode_running = threading.Event()
        self._idle = threading.Event()
        self._idle.set()
        self._segments: List[Dict] = []
        self._full: Optional[Dict] = None
        # Boundaries pushed by the recorder thread; drained by the audio
        # daemon thread.  deque is thread-safe for append/popleft per the
        # CPython docs.
        self._pending_boundaries: Deque[Tuple[int, str]] = deque()

    # ------------------------------------------------------------------
    # Session lifecycle
    # ------------------------------------------------------------------

    def start_session(self) -> None:
        """Spin up the daemon polling thread. Idempotent."""
        if self._session_running:
            return
        if not _PYAUDIO_AVAILABLE:
            _warn(
                "PyAudio not installed — audio recording disabled.\n"
                "Install with: uv sync --extra audio\n"
                "(also: apt install portaudio19-dev on fresh Ubuntu systems)"
            )
            return
        try:
            self._pa = pyaudio.PyAudio()
            # Verify default input device exists; raises IOError if none.
            if self._device_index is None:
                self._pa.get_default_input_device_info()
            else:
                self._pa.get_device_info_by_index(self._device_index)
        except (OSError, IOError) as e:
            _warn(
                f"No microphone available ({e}) — audio recording disabled.\n"
                "Recording will continue without audio."
            )
            if self._pa is not None:
                self._pa.terminate()
                self._pa = None
            return

        self._enabled = True
        self._session_running = True
        self._thread = threading.Thread(
            target=self._run, name="audio-recorder", daemon=True
        )
        self._thread.start()
        print("  ✓ AudioRecorder ready (continuous-with-segments).")

    def stop_session(self) -> None:
        """Stop the daemon thread and release the PyAudio context."""
        self._session_running = False
        self._episode_running.clear()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
        if self._pa is not None:
            self._pa.terminate()
            self._pa = None
        self._enabled = False

    # ------------------------------------------------------------------
    # Episode lifecycle
    # ------------------------------------------------------------------

    def start_episode(self, recording_dir: Path) -> None:
        """Mark a new episode active so the daemon thread opens the stream.

        Audio capture begins immediately — the recorder calls
        ``mark_boundary`` to push each subtask-boundary timestamp.
        """
        if not self._enabled:
            return
        self._episode_dir = Path(recording_dir)
        self._segments = []
        self._full = None
        self._pending_boundaries.clear()
        self._idle.set()
        self._episode_running.set()

    def mark_boundary(self, t_ns: int, clock: str) -> None:
        """Push a subtask-boundary timestamp into the audio stream.

        Called by the recorder thread on each pedal press, with the same
        ``(t_ns, clock)`` value it used for the matching ``event_markers``
        entry.  No-op when the recorder is disabled or no episode is active.
        """
        if not self._enabled or not self._episode_running.is_set():
            return
        self._pending_boundaries.append((int(t_ns), str(clock)))

    def stop_episode(self) -> None:
        """Signal end-of-episode; the daemon will close any open stream and save."""
        if not self._enabled:
            return
        self._episode_running.clear()

    # After this many seconds of waiting, log that the flush is still
    # running so the operator knows the verdict prompt delay is audio
    # finalisation rather than a hang.  Independent of the caller's
    # ``timeout``: if the caller waits less than this we never warn.
    _SOFT_WARN_S = 5.0

    def wait_until_idle(self, timeout: float = 60.0) -> bool:
        """Block up to ``timeout`` seconds for the daemon to flush any
        in-progress segment.

        Returns True if idle, False if ``timeout`` elapsed without the
        daemon settling.  When False, ``drain`` will return only what
        has been serialised so far — metadata.json may under-count, but
        every entry it does claim is backed by a real WAV on disk
        (segments and ``audio_full`` are recorded in state only after
        their WAV is written).
        """
        if not self._enabled:
            return True
        if timeout <= self._SOFT_WARN_S:
            return self._idle.wait(timeout=timeout)
        if self._idle.wait(timeout=self._SOFT_WARN_S):
            return True
        print(
            f"  ! Audio WAV flush still running after {self._SOFT_WARN_S:.0f}s; "
            f"waiting up to {timeout:.0f}s before giving up"
        )
        return self._idle.wait(timeout=timeout - self._SOFT_WARN_S)

    def drain(self) -> Dict:
        """Return and clear the audio metadata for the last episode.

        Returns ``{"audio_full": <dict or None>, "audio_segments": [...]}``.
        ``audio_full`` is the single concatenated WAV covering the whole
        episode (always written when at least one frame was captured).
        ``audio_segments`` is one entry per pedal press — empty when the
        operator never pressed during this episode.
        """
        out = {
            "audio_full": self._full,
            "audio_segments": list(self._segments),
        }
        self._full = None
        self._segments = []
        return out

    @property
    def enabled(self) -> bool:
        """True if the recorder will actually capture audio (PyAudio + device OK)."""
        return self._enabled

    # ------------------------------------------------------------------
    # Daemon thread
    # ------------------------------------------------------------------

    def _run(self) -> None:
        try:
            while self._session_running:
                if not self._episode_running.is_set():
                    time.sleep(0.05)
                    continue
                self._capture_one_episode()
        except (OSError, RuntimeError, ValueError) as e:
            # Expected failure modes from PyAudio / disk IO / clock reads.
            # Print loud and re-raise so the operator sees the daemon died
            # (rather than silently no-op'ing all subsequent episodes).
            print(f"  ! AudioRecorder thread died ({type(e).__name__}: {e})")
            raise
        finally:
            self._idle.set()

    def _capture_one_episode(self) -> None:
        """Open the stream at episode start, capture continuously, save on stop.

        Audio is captured from the moment ``start_episode`` is called until
        ``stop_episode`` clears ``_episode_running``, but **only the audio
        from the first press onwards is written to disk** — the
        pre-first-press period is treated as warm-up noise and discarded.
        Boundaries are drained from ``_pending_boundaries`` (pushed by the
        recorder thread via ``mark_boundary``), producing one WAV per
        inter-press interval plus a single ``audio_full.wav`` covering
        first-press → end-of-episode.
        """
        episode_dir = self._episode_dir
        if episode_dir is None:
            self._episode_running.clear()
            return

        frames: List[bytes] = []
        boundaries: List[Tuple[int, int, str, float]] = []
        # boundary tuple: (frame_index, t_ns, clock_label, wall_time)

        def _callback(in_data, frame_count, time_info, status, _f=frames):
            _f.append(in_data)
            return (None, _PYAUDIO_PA_CONTINUE)

        try:
            stream = self._pa.open(
                format=_PYAUDIO_FORMAT_INT16,
                channels=CHANNELS,
                rate=SAMPLE_RATE,
                input=True,
                input_device_index=self._device_index,
                frames_per_buffer=CHUNK,
                stream_callback=_callback,
            )
        except (OSError, IOError) as e:
            # Don't loop: clearing _episode_running stops _run from
            # immediately re-entering this function (which would spam
            # logs and re-attempt opens for the rest of the episode on
            # a permanently-failed device).  The next start_episode()
            # gives the daemon a fresh attempt.
            print(f"  ! AudioRecorder.open() failed ({e}); audio off for this episode")
            self._episode_running.clear()
            return

        self._idle.clear()
        print("  ✓ Audio stream open (warm-up noise discarded until first press)")

        try:
            while self._session_running and self._episode_running.is_set():
                # Drain every boundary the recorder pushed since the last
                # tick.  The frame index is "current end of buffer at the
                # moment we observed the boundary"; (ts_ns, clock) come
                # straight from the recorder so they bit-match the
                # corresponding event_markers entry.
                while self._pending_boundaries:
                    ts_ns, clock = self._pending_boundaries.popleft()
                    boundaries.append((len(frames), ts_ns, clock, time.time()))
                    print(
                        f"  ✓ Audio segment boundary #{len(boundaries)} "
                        f"@ ts={ts_ns} clock={clock}"
                    )
                time.sleep(0.05)
        finally:
            try:
                stream.stop_stream()
                stream.close()
            except (OSError, RuntimeError):
                # PyAudio's stop/close can raise on already-closed streams
                # or device disconnect.  Cleanup-path only — log and move on.
                print("  ! AudioRecorder stream cleanup raised; continuing")
            # Drain any boundary pushed in the gap between the last poll
            # and stop_episode() so the final segment isn't lost.
            while self._pending_boundaries:
                ts_ns, clock = self._pending_boundaries.popleft()
                boundaries.append((len(frames), ts_ns, clock, time.time()))

        try:
            self._save_audio(episode_dir, frames, boundaries)
        finally:
            self._idle.set()

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def _save_audio(
        self,
        episode_dir: Path,
        frames: List[bytes],
        boundaries: List[Tuple[int, int, str, float]],
    ) -> None:
        """Write per-press segment WAVs + a concatenated ``audio_full.wav``.

        Both shapes start at the **first pedal press** — pre-first-press
        frames are warm-up noise and discarded.  With N presses you get
        N segments (segment ``i`` runs from press ``i`` to press ``i+1``,
        last → end-of-episode) plus one ``audio_full.wav`` that is
        sample-identical to the concatenation of those segments.
        Nothing is written if the operator never pressed the pedal.
        """
        if not frames or not boundaries:
            return

        audio_dir = episode_dir / "audio"
        audio_dir.mkdir(parents=True, exist_ok=True)
        timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")

        first_idx, first_ts_ns, first_clock, first_wall = boundaries[0]

        # ── per-press segments (one WAV per inter-press interval) ─────────
        end_wall = time.time()
        boundaries_with_end = boundaries + [
            (len(frames), 0, first_clock, end_wall)  # ts_ns/clock unused for end
        ]
        for i in range(len(boundaries_with_end) - 1):
            start_idx, start_ts_ns, start_clock, start_wall = boundaries_with_end[i]
            end_idx, _, _, end_wall_i = boundaries_with_end[i + 1]
            seg_frames = frames[start_idx:end_idx]

            # Always emit one segment per pedal press, even when empty.
            # Skipping would break the contract that
            # ``len(audio_segments) == len(event_markers)`` (e.g. operator
            # presses the pedal then immediately stops the episode before
            # any post-boundary frame lands).  Empty-segment WAVs are
            # valid (header-only) and explicitly carry duration_s = 0.0
            # so downstream consumers can skip them if desired.
            wav_filename = f"audio_{i}_{timestamp_str}.wav"
            duration = _save_wav(audio_dir / wav_filename, seg_frames)
            sidecar = {
                "audio_file": wav_filename,
                "segment_id": i,
                "boundary_t_ns": start_ts_ns,
                "boundary_wall_time": start_wall,
                "end_wall_time": end_wall_i,
                "duration_s": round(duration, 3),
                "sample_rate": SAMPLE_RATE,
                "channels": CHANNELS,
                "clock": start_clock,
            }
            (audio_dir / f"audio_{i}_{timestamp_str}.json").write_text(
                json.dumps(sidecar, indent=2)
            )
            self._segments.append(
                {
                    "audio_file": wav_filename,
                    "segment_id": i,
                    "boundary_t_ns": start_ts_ns,
                    "duration_s": round(duration, 3),
                    "clock": start_clock,
                }
            )
            print(
                f"  ✓ Saved audio segment {i}: {wav_filename} "
                f"({duration:.1f}s, clock={start_clock})"
            )

        # ── audio_full.wav (concat of segments, starts at first press) ────
        full_wav_name = "audio_full.wav"
        full_duration = _save_wav(audio_dir / full_wav_name, frames[first_idx:])
        full_sidecar = {
            "audio_file": full_wav_name,
            "start_t_ns": first_ts_ns,
            "start_wall_time": first_wall,
            "duration_s": round(full_duration, 3),
            "sample_rate": SAMPLE_RATE,
            "channels": CHANNELS,
            "clock": first_clock,
        }
        (audio_dir / "audio_full.json").write_text(json.dumps(full_sidecar, indent=2))
        self._full = {
            "audio_file": full_wav_name,
            "start_t_ns": first_ts_ns,
            "duration_s": round(full_duration, 3),
            "clock": first_clock,
        }
        print(
            f"  ✓ Saved audio_full: {full_wav_name} "
            f"({full_duration:.1f}s, clock={first_clock})"
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _save_wav(path: Path, frames: List[bytes]) -> float:
    """Write the captured PyAudio frames to a WAV file. Returns duration (s)."""
    import wave  # stdlib

    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(CHANNELS)
        wf.setsampwidth(SAMPLE_WIDTH)
        wf.setframerate(SAMPLE_RATE)
        for frame in frames:
            wf.writeframes(frame)
    # Each callback frame is `CHUNK` samples at `SAMPLE_RATE` Hz.
    return len(frames) * CHUNK / SAMPLE_RATE
