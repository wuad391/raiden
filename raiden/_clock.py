"""Shared clock-source discriminators used in event_markers / audio_segments.

Both raiden.recorder and raiden.audio tag every recorded timestamp with
one of these strings so downstream consumers can detect markers that
fell off the camera clock (i.e. the wallclock fallback fired).
"""

CLOCK_CAMERA = "camera"
CLOCK_FALLBACK = "wallclock_fallback"
