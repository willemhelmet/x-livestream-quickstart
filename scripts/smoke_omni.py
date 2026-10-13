"""Generate one private 5.875-second Omni clip and save local media evidence.

Uses the direct session API and paid Reactor credits. Never reads X stream
credentials or sends to X. The session is closed on success, error, or timeout.
"""

import argparse
import asyncio
import contextlib
import json
import os
from pathlib import Path
import sys
import wave

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'runtime'))

import numpy as np
from dotenv import dotenv_values
from PIL import Image
from reactor_sdk import Reactor

from omni_contract import MODEL_NAME
from reactor_link import payload


async def check(key, output, timeout):
    reactor = Reactor(MODEL_NAME, api_key=key)
    loop = asyncio.get_running_loop()
    finished = asyncio.Event()
    evidence = dict(model=MODEL_NAME, videoFrames=0, audioSamples=0, audioPeak=0)
    images, chunks = [], []
    model_clip = {}
    failure = []

    def on_video(frame, frame_id, timestamp_us):
        evidence['videoFrames'] += 1
        evidence['width'], evidence['height'] = int(frame.shape[1]), int(frame.shape[0])
        if not images:
            images.append(frame.copy())

    def on_audio(frame, sample_rate, channels):
        evidence['audioSamples'] += int(frame.shape[0])
        evidence['sampleRate'], evidence['channels'] = sample_rate, channels
        evidence['audioPeak'] = max(evidence['audioPeak'], int(np.abs(frame.astype(np.int32)).max(initial=0)))
        chunks.append(frame.copy())

    def on_message(message):
        if not isinstance(message, dict):
            return
        body = payload(message)
        if not isinstance(body, dict):
            return
        if message.get('type') == 'clip_generated':
            model_clip.update(body.get('clip', {}))
        if message.get('type') == 'clip_finished':
            loop.call_soon_threadsafe(finished.set)
        if message.get('type') in ('clip_failed', 'command_error'):
            failure.append(message['type'])
            loop.call_soon_threadsafe(finished.set)

    reactor.track('main_video').on_frame(on_video)
    reactor.track('main_audio').on_frame(on_audio)
    reactor.on('message', on_message)
    try:
        async with asyncio.timeout(timeout):
            print('Connecting to FastH3-Omni…', flush=True)
            await reactor.connect()
            schema = await reactor.request_schema()
            output.mkdir(parents=True, exist_ok=True, mode=0o700)
            (output / 'schema.json').write_text(json.dumps(schema, indent=2))
            print('Authenticated session and served schema received.', flush=True)
            for command, data in (
                ('set_canvas', {'aspect': '16:9'}),
                ('set_flush_on_clip_end', {'enabled': False}),
                ('set_autoplay', {'enabled': True}),
                ('enqueue', {'prompt': 'A ceramic artist slowly turns a blue bowl under warm studio light. '
                             'The camera gently moves closer. A pottery wheel hums and rain taps the window.',
                             'seconds': 5.875}),
            ):
                response = await reactor.send_command(command, data)
                if response is None or (isinstance(response, dict) and response.get('type') == 'command_error'):
                    raise RuntimeError(f'{command} was refused')
            print('One clip queued; waiting for video and sound…', flush=True)
            await finished.wait()
            await asyncio.sleep(0.25)
            if failure:
                raise RuntimeError('Model reported ' + failure[-1])
            if not images or not chunks or evidence['audioPeak'] == 0:
                raise RuntimeError('Model did not deliver video plus non-silent audio')
            Image.fromarray(images[0]).save(output / 'first-frame.png')
            audio = np.concatenate(chunks, axis=0)
            with wave.open(str(output / 'audio.wav'), 'wb') as stream:
                stream.setnchannels(evidence['channels'])
                stream.setsampwidth(2)
                stream.setframerate(evidence['sampleRate'])
                stream.writeframes(audio.astype('<i2').tobytes())
            frames, seconds = model_clip.get('frames'), model_clip.get('seconds')
            if isinstance(frames, (int, float)) and isinstance(seconds, (int, float)) and seconds > 0:
                evidence['nominalFPS'] = frames / seconds
            evidence['healthy'] = True
            (output / 'result.json').write_text(json.dumps(evidence, indent=2))
            return evidence
    finally:
        with contextlib.suppress(Exception):
            await asyncio.wait_for(reactor.disconnect(), 10)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'test-results' / 'omni-smoke')
    parser.add_argument('--timeout', type=int, default=150)
    args = parser.parse_args()
    if not 30 <= args.timeout <= 300:
        parser.error('Use a timeout between 30 and 300 seconds.')
    settings = {**os.environ, **{k: v for k, v in dotenv_values(ROOT / '.env').items() if v is not None}}
    credentials = ROOT / '.local' / 'credentials.json'
    if credentials.is_file() and not credentials.is_symlink():
        settings.update(json.loads(credentials.read_text()))
    key = settings.get('REACTOR_API_KEY', '')
    if not key:
        parser.error('Add your Reactor key in the dashboard or REACTOR_API_KEY environment variable.')
    try:
        evidence = asyncio.run(check(key, args.output.resolve(), args.timeout))
    except Exception as error:
        # Provider exception bodies can contain credentials; report structured codes only.
        code = getattr(error, 'code', type(error).__name__)
        status = getattr(error, 'status', None)
        print(f'Private model check failed: {code}' + (f' (HTTP {status})' if status else ''), file=sys.stderr)
        raise SystemExit(1) from None
    print(json.dumps(evidence))
    print(f'Local evidence: {args.output.resolve()}')


if __name__ == '__main__':
    main()
