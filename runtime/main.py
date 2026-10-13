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
from chat.local import LocalChatSource
from config import Config, read_options, presets_dir, OMNI_MODELS
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
from studio_state import publish as publish_studio_state
from supervisor import parent_id, watch_parent

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
    """Read suggestions relayed by the loopback studio; X chat is not automatic."""
    inbox = os.environ.get('LOCAL_CHAT_INBOX')
    return [LocalChatSource(inbox)] if inbox else []


async def main() -> None:
    setup_logging()
    owner = parent_id()
    if owner is not None and os.getppid() != owner:
        logger.warning('Studio owner is gone; generation will not start.')
        return
    stopped = asyncio.Event()
    owner_task = asyncio.create_task(watch_parent(owner, stopped), name='studio-owner') if owner else None
    args = read_options()
    preset_name = args.preset or os.environ.get('PRESET', 'show.json')
    preset_path = Path(preset_name) if '/' in preset_name or preset_name.endswith('.json') else presets_dir() / f'{preset_name}.json'
    if json.loads(preset_path.read_text()).get('model') not in OMNI_MODELS:
        await prepare_show(preset_path)
    config = Config.load()
    if stopped.is_set():
        return

    link = ReactorLink(config)
    writer = FastNarrativeUpsampler if config.model in OMNI_MODELS else NarrativeUpsampler
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
    if owner_task:
        tasks.append(owner_task)
    if os.environ.get('PREVIEW_DIR'):
        tasks.append(asyncio.create_task(publish_studio_state(link, os.environ['PREVIEW_DIR']),
                                         name='studio-state'))
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
        if stopped.is_set():
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
                chat_command=None,
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
        if stopped.is_set():
            return
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
