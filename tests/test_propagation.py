"""Downstream propagation of event_markers + audio_segments.

These tests pin the contract that:
  * `_build_sequence_metadata` (called by `rd convert`) carries the two
    new fields from raw metadata into the converted sequence metadata.
  * The shardify subtask_index file is shaped {episode_id: {event_markers,
    audio_segments}} and only mentions episodes that actually have data.

The full converter / shardify pipelines need the heavy ML stack and
real recording data, so these tests target the small pure-python
seams that own propagation and skip the rest.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from raiden.converter import _build_sequence_metadata


# ---------------------------------------------------------------------------
# Converter — _build_sequence_metadata
# ---------------------------------------------------------------------------


def _seq_meta(rec_meta: dict, tmp_path: Path) -> dict:
    """Run _build_sequence_metadata with minimal inputs and read the result."""
    seq_dir = tmp_path / "seq"
    seq_dir.mkdir()
    _build_sequence_metadata(
        seq_dir=seq_dir,
        cameras=["scene"],
        frame_counts={"scene": 10},
        rec_meta=rec_meta,
        camera_infos={"scene": {"height": 720, "width": 1280}},
    )
    return json.loads((seq_dir / "metadata.json").read_text())


def test_sequence_metadata_propagates_event_markers(tmp_path):
    markers = [
        {"t": 1700000000123456789, "elapsed_s": 1.23, "clock": "camera"},
        {"t": 1700000000223456789, "elapsed_s": 2.23, "clock": "wallclock_fallback"},
    ]
    out = _seq_meta({"task_name": "x", "event_markers": markers}, tmp_path)
    assert out["event_markers"] == markers


def test_sequence_metadata_propagates_audio_segments(tmp_path):
    segments = [
        {
            "segment_id": 0,
            "audio_file": "audio_0_xxx.wav",
            "boundary_t_ns": 1700000000123456789,
            "duration_s": 4.523,
            "clock": "camera",
        }
    ]
    out = _seq_meta({"task_name": "x", "audio_segments": segments}, tmp_path)
    assert out["audio_segments"] == segments


def test_sequence_metadata_propagates_audio_full(tmp_path):
    full = {
        "audio_file": "audio_full.wav",
        "start_t_ns": 1700000000000000000,
        "duration_s": 12.5,
        "clock": "camera",
    }
    out = _seq_meta({"task_name": "x", "audio_full": full}, tmp_path)
    assert out["audio_full"] == full


def test_sequence_metadata_omits_keys_when_raw_has_none(tmp_path):
    out = _seq_meta({"task_name": "x"}, tmp_path)
    assert "event_markers" not in out
    assert "audio_segments" not in out
    assert "audio_full" not in out


def test_sequence_metadata_omits_keys_when_raw_has_empty_lists(tmp_path):
    """Empty lists / None shouldn't pollute the converted metadata —
    operators should be able to grep `audio_segments` / `audio_full` to
    find episodes that actually carry audio."""
    out = _seq_meta(
        {"task_name": "x", "event_markers": [], "audio_segments": [],
         "audio_full": None},
        tmp_path,
    )
    assert "event_markers" not in out
    assert "audio_segments" not in out
    assert "audio_full" not in out


# ---------------------------------------------------------------------------
# Shardify — subtask_index.json shape
# ---------------------------------------------------------------------------


def _build_subtask_index(ep_contexts: list) -> dict:
    """Mirror the projection done inside `run_shardify` so we can assert
    the JSON shape without standing up the full pipeline."""
    out: dict = {}
    for ctx in ep_contexts:
        markers = ctx.get("event_markers") or []
        segments = ctx.get("audio_segments") or []
        if markers or segments:
            out[ctx["episode_id"]] = {
                "event_markers": markers,
                "audio_segments": segments,
            }
    return out


def test_subtask_index_includes_episodes_with_markers_only():
    """No audio? Episode is still indexed."""
    eps = [{"episode_id": "ep1",
            "event_markers": [{"t": 1, "elapsed_s": 0.1, "clock": "camera"}],
            "audio_segments": []}]
    idx = _build_subtask_index(eps)
    assert "ep1" in idx
    assert idx["ep1"]["audio_segments"] == []


def test_subtask_index_includes_episodes_with_audio_only():
    eps = [{"episode_id": "ep1",
            "event_markers": [],
            "audio_segments": [{"segment_id": 0, "audio_file": "a.wav",
                                "boundary_t_ns": 1, "duration_s": 1.0,
                                "clock": "camera"}]}]
    idx = _build_subtask_index(eps)
    assert "ep1" in idx
    assert idx["ep1"]["event_markers"] == []


def test_subtask_index_skips_empty_episodes():
    eps = [
        {"episode_id": "ep1", "event_markers": [], "audio_segments": []},
        {"episode_id": "ep2",
         "event_markers": [{"t": 1, "elapsed_s": 0.1, "clock": "camera"}],
         "audio_segments": []},
    ]
    idx = _build_subtask_index(eps)
    assert "ep1" not in idx
    assert "ep2" in idx
