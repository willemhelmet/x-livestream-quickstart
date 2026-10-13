"""Small, credential-free model snapshots for the local dashboard."""

import asyncio
import json
import os
from pathlib import Path
import tempfile
import time

from group_tag import parse_group_tag


def scene_title(clip):
    if not isinstance(clip, dict):
        return None
    tag = parse_group_tag(clip.get('metadata', '')) or {}
    title = tag.get('title')
    return title[:120] if isinstance(title, str) else 'Next scene'


def snapshot(link):
    return dict(updatedAt=time.time(), connected=link.connected,
                videoReceived=link.video_frames > 0, audioReceived=link.audio_frames > 0,
                generationQueued=link.generation_queued, playoutQueued=link.playout_queued,
                currentScene=scene_title(link.current_clip),
                comingUp=[scene_title(clip) for clip in
                          (link.playout_clips + link.generation_clips)[:3]])


async def publish(link, directory):
    directory = Path(directory)
    destination = directory / 'model-status.json'
    while True:
        descriptor, temporary = tempfile.mkstemp(prefix='.model-status-', dir=directory)
        try:
            with os.fdopen(descriptor, 'w') as stream:
                json.dump(snapshot(link), stream)
            os.replace(temporary, destination)
        finally:
            Path(temporary).unlink(missing_ok=True)
        await asyncio.sleep(1)
