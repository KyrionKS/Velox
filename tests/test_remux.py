"""Tests for the merged remux pass (structured RemuxPlan -> ffmpeg args)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from velox.media import MediaInfo
from velox.pipeline import Pipeline
from velox.plugins.base import RemuxPlan


def media(streams: list[dict[str, Any]]) -> MediaInfo:
    probe = {"streams": streams, "format": {"duration": "100.0"}}
    return MediaInfo.from_probe(Path("movie.mkv"), probe, 1_000_000)


def v(i: int) -> dict[str, Any]:
    return {"index": i, "codec_type": "video", "codec_name": "h264"}


def a(i: int, lang: str = "eng", codec: str = "aac") -> dict[str, Any]:
    return {"index": i, "codec_type": "audio", "codec_name": codec, "tags": {"language": lang}}


def s(i: int, lang: str = "eng") -> dict[str, Any]:
    return {"index": i, "codec_type": "subtitle", "codec_name": "subrip", "tags": {"language": lang}}


def test_drop_only_builds_negative_free_args():
    m = media([v(0), a(1, "eng"), a(2, "jpn"), s(3, "eng")])
    args = Pipeline._build_remux_args(RemuxPlan(drop={2}), m)
    assert args == [
        "-map", "0:0", "-map", "0:1", "-map", "0:3",
        "-c", "copy", "-max_muxing_queue_size", "9999",
    ]


def test_output_indices_are_remapped_after_drops():
    # Two audio streams; dropping the first must shift the codec target.
    m = media([v(0), a(1, "eng"), a(2, "eng"), a(3, "eng")])
    plan = RemuxPlan(drop={1}, codecs={3: "aac"}, bitrates={3: "192k"})
    args = Pipeline._build_remux_args(plan, m)
    # remaining audio are 0:2 and 0:3 -> output audio indices a:0 and a:1
    assert args == [
        "-map", "0:0", "-map", "0:2", "-map", "0:3",
        "-c", "copy",
        "-c:a:1", "aac",
        "-b:a:1", "192k",
        "-max_muxing_queue_size", "9999",
    ]


def test_metadata_uses_output_specifier():
    m = media([v(0), a(1, "und"), a(2, "jpn")])
    plan = RemuxPlan(drop={2}, metadata={1: [("language", "eng"), ("title", "2.0")]})
    args = Pipeline._build_remux_args(plan, m)
    assert args == [
        "-map", "0:0", "-map", "0:1",
        "-c", "copy",
        "-metadata:s:a:0", "language=eng",
        "-metadata:s:a:0", "title=2.0",
        "-max_muxing_queue_size", "9999",
    ]


def test_dispositions_use_output_specifier():
    m = media([v(0), a(1, "eng"), a(2, "und")])
    plan = RemuxPlan(dispositions={1: "default", 2: "0"})
    args = Pipeline._build_remux_args(plan, m)
    assert args == [
        "-map", "0:0", "-map", "0:1", "-map", "0:2",
        "-c", "copy",
        "-disposition:a:0", "default",
        "-disposition:a:1", "0",
        "-max_muxing_queue_size", "9999",
    ]


def test_order_is_applied():
    m = media([v(0), a(1), s(2)])
    args = Pipeline._build_remux_args(RemuxPlan(order=[1, 0, 2]), m)
    assert args == [
        "-map", "0:1", "-map", "0:0", "-map", "0:2",
        "-c", "copy", "-max_muxing_queue_size", "9999",
    ]


def test_noop_plan_produces_no_args():
    m = media([v(0), a(1)])
    assert Pipeline._build_remux_args(RemuxPlan(), m) == []
    # order equal to the current order is also a no-op
    assert Pipeline._build_remux_args(RemuxPlan(order=[0, 1]), m) == []


def test_plan_merge_unions_and_overrides():
    first = RemuxPlan(drop={2}, metadata={1: [("language", "eng")]})
    second = RemuxPlan(order=[0, 1, 3], codecs={3: "aac"})
    third = RemuxPlan(drop={4})
    first.merge(second)
    first.merge(third)
    assert first.drop == {2, 4}
    assert first.order == [0, 1, 3]
    assert first.codecs == {3: "aac"}
    assert first.metadata == {1: [("language", "eng")]}


def test_filter_media_keeps_indices():
    m = media([v(0), a(1, "eng"), a(2, "jpn"), s(3, "eng")])
    filtered = Pipeline._filter_media(m, {2})
    assert [int(st["index"]) for st in filtered.streams] == [0, 1, 3]


def test_apply_plan_to_media_reflects_codec_and_metadata():
    m = media([v(0), a(1, "und", "ac3"), a(2, "jpn")])
    plan = RemuxPlan(drop={2}, codecs={1: "aac"}, metadata={1: [("language", "eng")]})
    updated = Pipeline._apply_plan_to_media(m, plan)
    assert [int(st["index"]) for st in updated.streams] == [0, 1]
    assert updated.streams[1]["codec_name"] == "aac"
    assert updated.streams[1]["tags"]["language"] == "eng"


def test_video_only_preserved_in_multiple_streams():
    # image streams must be kept when reordering
    m = media([v(0), a(1), {"index": 2, "codec_type": "video", "codec_name": "png"}])
    args = Pipeline._build_remux_args(RemuxPlan(order=[1, 0, 2]), m)
    assert args[:6] == ["-map", "0:1", "-map", "0:0", "-map", "0:2"]
