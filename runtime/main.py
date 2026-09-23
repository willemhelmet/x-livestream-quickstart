"""One show seed → next-moment writer → reference model → paced live output.

The writer is replaceable via narrative.next_scene; image uploads and media
transport remain in the worker. Configured chat suggestions steer future shots,
and the idle writer keeps the story progressing without chat.
"""

from __future__ import annotations

import asyncio
import logging
import warnings
import signal

from chat import ChatSource
from config import Config, read_options, presets_dir, FAST_MODEL
from autoseed import prepare_show
from pathlib import Path
import os
import json
from director import Director
from moderator import Moderator
from overlay import StreamStatusOverlay
from pacer import Pacer
from reactor_link import MODEL_FPS, MODEL_SAMPLE_RATE, ReactorLink
from sinks import AudioFormat, VideoFormat, make_sink
from narrative import NarrativeUpsampler
from fast_narrative import FastNarrativeUpsampler

logger = logging.getLogger("streaming-client")


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    # WebRTC internals are chatty at INFO and alarming at their defaults.
    logging.getLogger("aiortc.codecs.vpx").setLevel(logging.ERROR)
    logging.getLogger("aiortc.codecs.h264").setLevel(logging.ERROR)
    logging.getLogger("aioice.ice").setLevel(logging.WARNING)
    warnings.filterwarnings("ignore", category=DeprecationWarning)


def build_chat_sources(config: Config, commands: tuple[str, ...]) -> list[ChatSource]:
    """Audience adapters are intentionally disconnected during rehearsal."""
    return []


async def main() -> None:
    setup_logging()
    args = read_options()
    preset_name = args.preset or os.environ.get('PRESET', 'show.json')
    preset_path = Path(preset_name) if '/' in preset_name or preset_name.endswith('.json') else presets_dir() / f'{preset_name}.json'
    if json.loads(preset_path.read_text()).get('model') != FAST_MODEL:
        await prepare_show(preset_path)
    config = Config.load()

    link = ReactorLink(config)
    writer = FastNarrativeUpsampler if config.model == FAST_MODEL else NarrativeUpsampler
    upsampler = writer(
        narrative=config.narrative,
        api_key=config.writer_api_key,
        model=config.writer_model,
        style=config.style,
        base_url=config.writer_base_url,
    )
    moderator = Moderator(
        api_key=config.moderation_api_key,
        model=config.moderation_model,
        enabled=config.moderation_enabled,
        base_url=config.moderation_base_url,
    )
    if not moderator.enabled:
        logger.warning(
            "moderation is DISABLED (MODERATION_ENABLED=0) — every chat "
            "prompt reaches the upsampler unchecked"
        )
    director = Director(
        link,
        upsampler,
        moderator,
        cooldown_s=config.chat_cooldown_s,
        idle_prompts=config.idle_prompts,
        idle_queue_target=config.idle_queue_target,
    )
    link.add_listener(upsampler.record_played)
    chat_sources = build_chat_sources(config, (config.chat_command,))
    route_chat = director.submit
    if not chat_sources:
        logger.warning(
            "local rehearsal: audience chat is disconnected; the idle writer keeps the show moving"
        )
    sink = make_sink(
        config.sink,
        rtmp_url=config.rtmp_url,
        rtmp_video_bitrate_k=config.rtmp_video_bitrate_k,
        rtmp_output_width=config.rtmp_output_width,
        rtmp_output_height=config.rtmp_output_height,
        rtmp_output_fps=config.rtmp_output_fps,
    )

    tasks: list[asyncio.Task] = [
        asyncio.create_task(link.run(), name="reactor-link"),
        asyncio.create_task(director.run(), name="director"),
        asyncio.create_task(director.run_playout(), name="playout"),
    ]
    # Gated here because main treats any finished task as a shutdown signal,
    # and run_idle returns immediately when the target is 0. A preset with no
    # idle prompts still gets the task: the filler idles until a `!switch`
    # brings prompts.
    if config.idle_queue_target > 0:
        tasks.append(asyncio.create_task(director.run_idle(), name="idle-filler"))
    else:
        logger.info("idle filler off (IDLE_QUEUE_TARGET=0)")
    tasks += [
        asyncio.create_task(source.run(route_chat), name=f"chat-{source.name}")
        for source in chat_sources
    ]

    stopped = asyncio.Event()
    loop = asyncio.get_running_loop()
    loop.add_signal_handler(signal.SIGTERM, stopped.set)
    stop_task = asyncio.create_task(stopped.wait(), name="shutdown")
    tasks.append(stop_task)
    first_state = asyncio.create_task(link.wait_first_state(), name="first-state")
    try:
        # The sink's geometry comes from the deployment (state_update), so the
        # pacer starts only once the first session is up. From then on it and
        # the sink survive every reconnect.
        done, _ = await asyncio.wait([first_state, *tasks], return_when=asyncio.FIRST_COMPLETED)
        if stop_task in done:
            return
        for task in done:
            if task is not first_state:
                raise RuntimeError(f"Startup task {task.get_name()} stopped") from task.exception()
        await first_state
        width, height = link.canvas
        # The overlay to broadcast is a code decision; swap the class here.
        overlay = (
            StreamStatusOverlay(
                link,
                chat_command=config.chat_command if chat_sources else None,
            )
            if config.overlay_enabled
            else None
        )
        pacer = Pacer(
            sink,
            VideoFormat(width=width, height=height, fps=MODEL_FPS),
            AudioFormat(sample_rate=MODEL_SAMPLE_RATE, channels=1),
            overlay=overlay,
        )
        link.attach_pacer(pacer)
        tasks.append(asyncio.create_task(pacer.run(), name="pacer"))
        logger.info(
            "streaming %dx%d@%dfps to sink=%s (overlay %s, preset %r) — "
            "chat command %r on %s",
            width, height, MODEL_FPS, config.sink,
            "on" if overlay else "off",
            config.preset_name,
            config.chat_command,
            ", ".join(s.name for s in chat_sources) or "nothing",
        )

        # Run until a task dies (none should) or the process is interrupted.
        done, _pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            if task is not stop_task:
                raise RuntimeError(f"Task {task.get_name()} stopped") from task.exception()
    finally:
        first_state.cancel()
        await asyncio.gather(first_state, return_exceptions=True)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        for source in chat_sources:
            try:
                await source.close()
            except Exception:
                pass
        await sink.stop()
        logger.info("shut down cleanly")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
