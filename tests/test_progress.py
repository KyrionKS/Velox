"""Tests for ffmpeg progress parsing and the human-readable size formatter."""

from __future__ import annotations

from velox.events import fmt_bytes
from velox.ffmpeg import _parse_progress, _parse_timecode


def test_parse_progress_line():
    assert _parse_progress("out_time_us=1500000") == ("out_time_us", "1500000")
    assert _parse_progress("progress=continue") == ("progress", "continue")
    assert _parse_progress("frame=10") == ("frame", "10")
    assert _parse_progress("nonsense-with-no-equals") is None
    assert _parse_progress("") is None


def test_parse_timecode():
    assert _parse_timecode("01:02:03.500000") == 3723.5
    assert _parse_timecode("00:00:00.000000") == 0.0
    assert _parse_timecode("bad") == 0.0


def test_fmt_bytes():
    assert fmt_bytes(0) == "0 B"
    assert fmt_bytes(999) == "999 B"
    assert fmt_bytes(1000) == "1.00 KB"
    assert fmt_bytes(1_500_000) == "1.50 MB"
    assert fmt_bytes(2_000_000_000) == "2.00 GB"
