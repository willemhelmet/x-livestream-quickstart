"""Output sinks. `make_sink` is the one place a sink name maps to a class.

Adding a destination (LiveKit, an SFU, a file recorder, ...):
  1. Implement `StreamSink` (see `base.py` for the contract) in a new module.
  2. Add a branch to `make_sink` and a value for `SINK` in `.env.example`.
  3. Document it in the README's sink table.
"""

from __future__ import annotations

from .base import AudioFormat, StreamSink, VideoFormat
from .noop import NoOpSink
from .encoder import LocalPreviewSink

__all__ = ["AudioFormat", "NoOpSink", "LocalPreviewSink", "StreamSink", "VideoFormat", "make_sink"]


def make_sink(
    name: str,
    *,
    rtmp_url: str | None = None,
    rtmp_video_bitrate_k: int = 4500,
    rtmp_output_width: int = 1280,
    rtmp_output_height: int = 720,
    rtmp_output_fps: int = 30,
) -> StreamSink:
    """Build the sink named by config."""
    if name == "noop":
        return NoOpSink()
    if name == "preview":
        import os
        return LocalPreviewSink(
            os.environ["PREVIEW_DIR"],
            video_bitrate_k=rtmp_video_bitrate_k,
            output_width=rtmp_output_width,
            output_height=rtmp_output_height,
            output_fps=rtmp_output_fps,
        )
    raise ValueError(f"unknown sink {name!r}")
