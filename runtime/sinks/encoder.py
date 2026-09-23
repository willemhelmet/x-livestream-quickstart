"""Local MP4 preview encoder adapted from the starter’s paced A/V sink.

Raw RGB video and PCM audio travel through separate bounded writer queues.
FFmpeg normalizes geometry and frame rate, then writes two-second H.264/AAC
clips and a CSV manifest. Output protocols are restricted to file and pipe.
A failed encoder ends rehearsal instead of reconnecting or overwriting clips.
Requires FFmpeg and POSIX inherited pipes (macOS/Linux).
"""

from __future__ import annotations

import collections
import logging
import os
import queue
import shutil
import subprocess
import threading
import time

import numpy as np

from .base import AudioFormat, StreamSink, VideoFormat

logger = logging.getLogger(__name__)

# Restart policy for a dying ffmpeg.
_RESTART_COOLDOWN_S = 2.0
_MAX_CONSECUTIVE_FAILURES = 5

# Bounded writer queues: ~2 seconds of stream each. Deep enough to ride out an
# encoder hiccup, shallow enough that overflow (dropping oldest) barely shows.
_QUEUE_SECONDS = 2.0


def _output_filter(width: int, height: int, fps: int) -> str:
    """Letterbox the model canvas into a platform-compatible constant rate."""
    return (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,fps={fps}"
    )


class _PipeWriter(threading.Thread):
    """Feed one ffmpeg input pipe from a bounded queue, off the event loop."""

    def __init__(self, name: str, maxsize: int) -> None:
        super().__init__(name=f"preview-{name}", daemon=True)
        self.queue: queue.Queue[bytes | None] = queue.Queue(maxsize=maxsize)
        self.pipe: object | None = None  # a writable binary file object
        self.broken = threading.Event()
        self.dropped = 0
        self._lock = threading.Lock()

    def attach(self, pipe) -> None:
        """Point the writer at a fresh pipe (after an ffmpeg restart)."""
        with self._lock:
            self.pipe = pipe
            self.broken.clear()

    def submit(self, payload: bytes) -> None:
        """Enqueue bytes; drop the oldest entry instead of ever blocking."""
        try:
            self.queue.put_nowait(payload)
        except queue.Full:
            try:
                self.queue.get_nowait()
                self.dropped += 1
            except queue.Empty:
                pass
            try:
                self.queue.put_nowait(payload)
            except queue.Full:
                self.dropped += 1

    def run(self) -> None:
        while True:
            payload = self.queue.get()
            if payload is None:  # shutdown sentinel
                return
            with self._lock:
                pipe = self.pipe
            if pipe is None or self.broken.is_set():
                continue  # ffmpeg is down; discard until it is restarted
            try:
                pipe.write(payload)
            except (BrokenPipeError, OSError, ValueError):
                # ValueError: write to a closed file during a restart race.
                self.broken.set()

    def close(self) -> None:
        # Never block the event loop behind an encoder that stopped reading.
        try:
            self.queue.put_nowait(None)
        except queue.Full:
            try:
                self.queue.get_nowait()
            except queue.Empty:
                pass
            self.queue.put_nowait(None)


class LocalPreviewSink(StreamSink):
    """Encode to local segmented MP4 only. No network output is implemented."""

    def __init__(
        self,
        url: str,
        video_bitrate_k: int = 4500,
        output_width: int = 1280,
        output_height: int = 720,
        output_fps: int = 30,
    ) -> None:
        if shutil.which("ffmpeg") is None:
            raise RuntimeError("ffmpeg not found on PATH; install it first")
        from pathlib import Path
        directory = Path(url)
        if not directory.is_absolute() or not directory.is_dir() or "://" in url:
            raise ValueError("Preview requires an existing absolute local directory")
        self._directory = directory
        self._url = str(directory / "clip-%06d.mp4")
        self._bitrate_k = video_bitrate_k
        self._output_width = output_width
        self._output_height = output_height
        self._output_fps = output_fps
        self._video: VideoFormat | None = None
        self._audio: AudioFormat | None = None
        self._process: subprocess.Popen[bytes] | None = None
        self._audio_write_fd: int | None = None
        self._video_writer: _PipeWriter | None = None
        self._audio_writer: _PipeWriter | None = None
        self._stderr_tail: collections.deque[str] = collections.deque(maxlen=40)
        self._failures = 0
        self._last_start_attempt = 0.0
        self._frames_sent = 0
        self._dead = False

    # ------------------------------------------------------------ lifecycle

    async def start(self, video: VideoFormat, audio: AudioFormat) -> None:
        self._video = video
        self._audio = audio
        self._video_writer = _PipeWriter(
            "video", maxsize=int(video.fps * _QUEUE_SECONDS)
        )
        # Audio arrives once per video tick, so the same depth covers it.
        self._audio_writer = _PipeWriter(
            "audio", maxsize=int(video.fps * _QUEUE_SECONDS)
        )
        self._video_writer.start()
        self._audio_writer.start()
        self._spawn_ffmpeg()

    def _spawn_ffmpeg(self) -> None:
        assert self._video is not None and self._audio is not None
        video, audio = self._video, self._audio
        self._last_start_attempt = time.monotonic()

        audio_read_fd, audio_write_fd = os.pipe()
        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "warning", "-nostdin",
            "-thread_queue_size", "512",
            # --- video input: raw rgb24 frames on stdin ---
            "-f", "rawvideo", "-pix_fmt", "rgb24",
            "-s", f"{video.width}x{video.height}",
            "-r", str(video.fps),
            "-i", "pipe:0",
            # --- audio input: raw int16 PCM on the inherited pipe ---
            "-thread_queue_size", "512", "-f", "s16le",
            "-ar", str(audio.sample_rate),
            "-ac", str(audio.channels),
            "-i", f"pipe:{audio_read_fd}",
            "-map", "0:v", "-map", "1:a",
            # --- video encode ---
            "-c:v", "libx264", "-preset", "veryfast", "-tune", "zerolatency",
            "-pix_fmt", "yuv420p",
            "-vf", _output_filter(
                self._output_width, self._output_height, self._output_fps
            ),
            "-r", str(self._output_fps),
            "-g", str(self._output_fps * 2),  # keyframe every 2 s
            "-b:v", f"{self._bitrate_k}k",
            "-maxrate", f"{int(self._bitrate_k * 1.2)}k",
            "-bufsize", f"{self._bitrate_k * 2}k",
            # --- audio encode ---
            "-c:a", "aac", "-b:a", "128k", "-ar", "44100", "-ac", "2",
            # --- output ---
            "-f", "segment", "-segment_time", "2", "-reset_timestamps", "1",
            "-segment_list", str(self._directory / "clips.csv"),
            "-segment_list_type", "csv", "-segment_list_size", "60", "-segment_format", "mp4",
            "-segment_format_options", "movflags=+faststart",
            "-protocol_whitelist", "file,pipe", self._url,
        ]
        try:
            self._process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                pass_fds=(audio_read_fd,),
            )
        except Exception:
            os.close(audio_write_fd)
            raise
        finally:
            # The child inherited its copy; the parent's read end must go.
            os.close(audio_read_fd)

        self._audio_write_fd = audio_write_fd
        audio_pipe = os.fdopen(audio_write_fd, "wb", buffering=0)
        assert self._video_writer and self._audio_writer
        self._video_writer.attach(self._process.stdin)
        self._audio_writer.attach(audio_pipe)

        threading.Thread(
            target=self._drain_stderr, args=(self._process,), daemon=True,
            name="preview-stderr",
        ).start()
        logger.info(
            "[preview] ffmpeg started: %dx%d@%dfps -> %dx%d@%dfps -> %s",
            video.width, video.height, video.fps,
            self._output_width, self._output_height, self._output_fps,
            _redact(self._url),
        )

    def _drain_stderr(self, process: subprocess.Popen[bytes]) -> None:
        assert process.stderr is not None
        for raw in process.stderr:
            line = raw.decode(errors="replace").rstrip()
            if line:
                self._stderr_tail.append(line)

    # ----------------------------------------------------------- restarting

    def _ensure_running(self) -> bool:
        """True when ffmpeg is up; otherwise try to restart it (rate-limited)."""
        if self._dead:
            return False
        process = self._process
        writers_broken = bool(
            (self._video_writer and self._video_writer.broken.is_set())
            or (self._audio_writer and self._audio_writer.broken.is_set())
        )
        if process is not None and process.poll() is None and not writers_broken:
            return True

        if process is not None and (process.poll() is not None or writers_broken):
            tail = "\n".join(list(self._stderr_tail)[-8:])
            logger.warning(
                "[preview] ffmpeg died (exit=%s)%s",
                process.poll(),
                f"\n{tail}" if tail else "",
            )
            self._teardown_process()
            # A failed local encoder must not overwrite earlier preview clips.
            self._dead = True
            raise RuntimeError("Local preview encoder stopped; stop and start a new rehearsal")

        if time.monotonic() - self._last_start_attempt < _RESTART_COOLDOWN_S:
            return False
        try:
            self._spawn_ffmpeg()
            self._failures = 0
            return True
        except Exception as error:
            self._failures += 1
            logger.error(
                "[preview] restart failed (%d/%d): %s",
                self._failures, _MAX_CONSECUTIVE_FAILURES, error,
            )
            if self._failures >= _MAX_CONSECUTIVE_FAILURES:
                logger.error("[preview] giving up; sink is dead")
                self._dead = True
            return False

    def _teardown_process(self) -> None:
        process, self._process = self._process, None
        if process is None:
            return
        try:
            process.terminate()
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)
        except ProcessLookupError:
            pass
        for writer in (self._video_writer, self._audio_writer):
            if writer and writer.pipe:
                with writer._lock:
                    try:
                        writer.pipe.close()
                    except (OSError, ValueError):
                        pass
                    writer.pipe = None
        if process.stderr:
            process.stderr.close()
        for stream in (process.stdin,):
            try:
                if stream:
                    stream.close()
            except Exception:
                pass
        if self._audio_write_fd is not None:
            # The fdopen() wrapper owns the fd; closing it via the writer's
            # broken pipe path is fine, but make sure it cannot leak.
            self._audio_write_fd = None
        try:
            process.terminate()
        except Exception:
            pass

    # ------------------------------------------------------------- delivery

    def send_video(self, frame: np.ndarray) -> None:
        if not self._ensure_running():
            return
        video = self._video
        assert video is not None and self._video_writer is not None
        if frame.shape[0] != video.height or frame.shape[1] != video.width:
            # Never write mismatched bytes: one wrong frame garbles the rest
            # of the stream. The pacer should have normalized geometry.
            logger.error(
                "[preview] refusing %sx%s frame (expected %dx%d)",
                frame.shape[1], frame.shape[0], video.width, video.height,
            )
            return
        if not frame.flags["C_CONTIGUOUS"]:
            frame = np.ascontiguousarray(frame)
        self._video_writer.submit(frame.tobytes())
        self._frames_sent += 1
        if self._frames_sent % (video.fps * 60) == 0:
            logger.info(
                "[preview] %d frames sent (dropped: %d video / %d audio)",
                self._frames_sent,
                self._video_writer.dropped,
                self._audio_writer.dropped if self._audio_writer else 0,
            )

    def send_audio(self, samples: np.ndarray) -> None:
        if self._dead or self._audio_writer is None:
            return
        self._audio_writer.submit(
            np.ascontiguousarray(samples, dtype=np.int16).tobytes()
        )

    async def stop(self) -> None:
        self._dead = True
        for writer in (self._video_writer, self._audio_writer):
            if writer:
                writer.close()
        import asyncio
        await asyncio.to_thread(self._teardown_process)
        logger.info("[preview] stopped after %d frames", self._frames_sent)

    @property
    def alive(self) -> bool:
        return not self._dead


def _redact(url: str) -> str:
    """Hide the stream key (the last path segment) in logs."""
    head, _, key = url.rpartition("/")
    if not head or len(key) <= 4:
        return url
    return f"{head}/…{key[-4:]}"
