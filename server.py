"""Loopback control room with local preview and explicitly armed X publishing."""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import csv
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import signal
import sys
import tempfile
import time
from urllib.parse import unquote, urlsplit
import webbrowser

from aiohttp import web
from dotenv import dotenv_values
from publisher import Publisher, validate_destination

ROOT = Path(__file__).resolve().parent
MODEL = 'reactor/h3-reference-to-video-turbo-realtime'
FAST_MODEL = 'reactor/fast-h3'
MAX_IMAGE = 10 * 1024 * 1024
CONTROLLER = web.AppKey('controller', object)
KEY_NAMES = ('REACTOR_API_KEY', 'XAI_API_KEY')


def validate_keys(value):
    if not isinstance(value, dict) or any(key not in KEY_NAMES for key in value):
        raise ValueError('Only Reactor and xAI API keys are supported.')
    if any(not isinstance(key, str) or len(key) > 2048 or
           any(ord(char) < 33 or ord(char) > 126 for char in key) for key in value.values()):
        raise ValueError('API keys must contain only printable characters without spaces, up to 2,048 characters.')
    return value


def test_signal_command(directory: Path, duration: int | None) -> list[str]:
    """All inputs are generated in FFmpeg; all outputs are local files."""
    return ['ffmpeg', '-hide_banner', '-loglevel', 'warning', '-nostdin',
            '-re', '-f', 'lavfi', '-i', 'testsrc2=size=1280x720:rate=30',
            '-f', 'lavfi', '-i', 'anullsrc=r=44100:cl=stereo',
            *(['-t', str(duration)] if duration is not None else []), '-map', '0:v', '-map', '1:a',
            '-c:v', 'libx264', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p',
            '-b:v', '2000k', '-g', '60', '-sc_threshold', '0',
            '-c:a', 'aac', '-b:a', '128k', '-f', 'segment',
            '-segment_time', '2', '-reset_timestamps', '1',
            '-segment_list', str(directory / 'clips.csv'), '-segment_list_type', 'csv', '-segment_list_size', '60',
            '-segment_format', 'mp4', '-segment_format_options', 'movflags=+faststart',
            '-protocol_whitelist', 'file,pipe', str(directory / 'clip-%06d.mp4')]


class Studio:
    def __init__(self, directory: Path, env=None):
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.env = dict(os.environ if env is None else env)
        self.key_file = self.directory / 'credentials.json'
        self.saved_keys = {}
        self.destination_file = self.directory / 'x-destination.json'
        self.destination = dict(serverUrl='', streamKey='')
        if self.destination_file.exists():
            if self.destination_file.is_symlink():
                raise ValueError('The X destination file must not be a symlink.')
            self.destination = validate_destination(json.loads(self.destination_file.read_text()))
        self.publisher = Publisher()
        if self.key_file.exists():
            if self.key_file.is_symlink():
                raise ValueError('The local credentials file must not be a symlink.')
            self.saved_keys = validate_keys(json.loads(self.key_file.read_text()))
            self.env.update(self.saved_keys)
        self.lock = asyncio.Lock()
        self.process = self.monitor = self.timer = None
        self.phase, self.source = 'stopped', 'test_signal'
        self.session_id = self.started_at = self.ended_at = None
        self.duration = None
        self.run_dir = None
        self.show = self.show_path = None
        self.logs, self.events = [], []
        self.counts = dict(messagesReceived=0, accepted=0, ignored=0, deferred=0)
        self.last_accepted = 0.0

    def log(self, text, level='info'):
        if self.destination['streamKey']:
            text = text.replace(self.destination['streamKey'], '[redacted]')
        for key, value in self.env.items():
            if value and any(word in key.upper() for word in ('KEY', 'TOKEN', 'SECRET', 'PASSWORD', 'RTMP')):
                text = text.replace(value, '[redacted]')
        text = re.sub(r'(?:https?|rtmps?)://\S+', '[remote endpoint]', text)
        self.logs.append(dict(at=time.time(), level=level, message=text[:1500]))
        self.logs = self.logs[-100:]

    def segments(self):
        if not self.run_dir or not (self.run_dir / 'clips.csv').exists():
            return []
        result = []
        with (self.run_dir / 'clips.csv').open() as stream:
            for row in csv.reader(stream):
                if len(row) != 3 or not re.fullmatch(r'clip-\d{6,}\.mp4', row[0]):
                    continue
                try:
                    duration = float(row[2]) - float(row[1])
                except ValueError:
                    continue
                if duration > 0 and (self.run_dir / row[0]).is_file():
                    result.append(dict(name=row[0], duration=duration,
                                       url=f'/media/{self.session_id}/{row[0]}'))
        # A continuous preview keeps only its recent playback window. The file
        # currently being encoded has a higher sequence number and is untouched.
        if result:
            first = min(int(s['name'][5:-4]) for s in result)
            for clip in self.run_dir.glob('clip-*.mp4'):
                if re.fullmatch(r'clip-\d{6,}\.mp4', clip.name) and int(clip.name[5:-4]) < first:
                    clip.unlink(missing_ok=True)
        return result

    def status(self):
        segments = self.segments()
        return dict(state=self.phase, source=self.source, sessionId=self.session_id,
                    startedAt=self.started_at, autoStopSeconds=self.duration,
                    elapsedSeconds=round((self.ended_at or time.time()) - self.started_at, 1) if self.started_at else 0,
                    media=dict(segments=segments, secondsAvailable=round(sum(s['duration'] for s in segments), 1)),
                    capabilities=dict(ffmpeg=bool(shutil.which('ffmpeg')),
                                      h3=all(importlib.util.find_spec(m) for m in ('reactor_sdk', 'openai', 'numpy', 'PIL'))),
                    credentials={key: bool(self.env.get(key)) for key in KEY_NAMES},
                    writer=dict(provider='xAI', model='grok-4.7', verified=False),
                    show=self.show, logs=self.logs[-60:], events=self.events[-50:], metrics=self.counts,
                    x=dict(serverUrl=self.destination['serverUrl'], hasStreamKey=bool(self.destination['streamKey']),
                           configured=bool(self.destination['streamKey'] and self.destination['serverUrl']),
                           state=self.publisher.state, error=self.publisher.error,
                           connected=self.publisher.state == 'sending', publishingEnabled=True))

    async def save_destination(self, updates):
        async with self.lock:
            if self.publisher.active:
                raise ValueError('Stop sending to X before changing the destination.')
            if not isinstance(updates, dict) or set(updates) != {'serverUrl', 'streamKey'}:
                raise ValueError('Provide a server URL and stream key.')
            if updates['serverUrl'] and updates['streamKey'] == '':
                updates = {**updates, 'streamKey': self.destination['streamKey']}
            destination = validate_destination(updates)
            descriptor, temporary = tempfile.mkstemp(prefix='.x-destination-', dir=self.directory)
            try:
                with os.fdopen(descriptor, 'w') as stream:
                    json.dump(destination, stream)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, self.destination_file)
            finally:
                Path(temporary).unlink(missing_ok=True)
            self.destination = destination
            return self.status()

    async def start_publishing(self, confirm_send=False):
        async with self.lock:
            if confirm_send is not True:
                raise ValueError('Confirm Send to X before transmitting video.')
            self.publisher.start(self, self.destination)
            return self.status()

    async def stop_publishing(self):
        async with self.lock:
            await self.publisher.stop()
            return self.status()

    def child_env(self):
        # No destination, chat credentials, parent .env, or setup passwords reach the worker.
        allowed = ('PATH', 'TMPDIR', 'SYSTEMROOT', 'SSL_CERT_FILE', 'SSL_CERT_DIR',
                   'REACTOR_API_KEY', 'XAI_API_KEY')
        result = {key: str(self.env[key]) for key in allowed if self.env.get(key)}
        result.update(PYTHONUNBUFFERED='1', PYTHON_DOTENV_DISABLED='1',
                      PREVIEW_DIR=str(self.run_dir), SINK='preview',
                      REACTOR_MODEL=FAST_MODEL if self.source == 'fast_h3' else MODEL)
        return result

    async def save_keys(self, updates):
        updates = validate_keys(updates)
        if not updates:
            raise ValueError('Enter at least one API key.')
        async with self.lock:
            if self.phase in ('starting', 'running', 'stopping'):
                raise ValueError('Stop the test before changing API keys.')
            saved = {**self.saved_keys, **updates}
            # Atomic, owner-only local storage. Never rewrite the user's .env.
            descriptor, temporary = tempfile.mkstemp(prefix='.credentials-', dir=self.directory)
            try:
                with os.fdopen(descriptor, 'w') as stream:
                    json.dump(saved, stream)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, self.key_file)
            finally:
                Path(temporary).unlink(missing_ok=True)
            self.saved_keys = saved
            self.env.update(updates)
            self.log('API key settings saved locally. Keys have not been verified with the providers.')
            return self.status()

    async def start(self, source='test_signal', duration=None, confirm_paid=False, premise=None, use_starting_frame=False):
        async with self.lock:
            opening_image = None
            if self.process and self.process.returncode is None:
                raise ValueError('A rehearsal is already running. Stop it before starting another.')
            await self.publisher.stop()
            if source not in ('test_signal', 'h3', 'fast_h3'):
                raise ValueError('Choose Test signal, H3 Turbo Realtime or FastH3.')
            if duration is not None and (type(duration) is not int or not 5 <= duration <= 300):
                raise ValueError('Use no duration for continuous playback, or 5–300 seconds for a timed check.')
            if not shutil.which('ffmpeg'):
                raise ValueError('Install FFmpeg and restart the studio before rehearsing.')
            if source in ('h3', 'fast_h3'):
                if confirm_paid is not True:
                    raise ValueError('Starting generation requires acknowledgment of paid Reactor and xAI usage.')
                state = self.status()
                if not state['capabilities']['h3'] or not all(state['credentials'].values()):
                    raise ValueError('Install requirements.txt and add Reactor and xAI keys under API keys.')
            if source == 'fast_h3':
                if not isinstance(premise, str) or not premise.strip() or len(premise) > 2000:
                    raise ValueError('Describe a scene in 1–2,000 characters for FastH3.')
                if type(use_starting_frame) is not bool:
                    raise ValueError('Starting frame must be enabled or disabled.')
                if use_starting_frame and not self.show_path:
                    raise ValueError('Choose a starting image, or turn off Use starting frame.')
                if use_starting_frame:
                    original = json.loads(self.show_path.read_text())
                    reference = (original['images'][0]['reference'] if original.get('version') == 3
                                 else original['narrative']['characters'][0]['reference'])
                    if not isinstance(reference, str) or not re.fullmatch(r'references/character-1\.(png|jpg|webp)', reference):
                        raise ValueError('Upload the starting image again.')
                    opening_image = (self.show_path.parent / reference).resolve()
                    if not opening_image.is_relative_to(self.show_path.parent / 'references') or not opening_image.is_file():
                        raise ValueError('Upload the starting image again.')
            if source == 'h3':
                if not self.show_path:
                    raise ValueError('Upload a reference image first.')
                if premise is not None:
                    if not isinstance(premise, str) or len(premise) > 2000:
                        raise ValueError('Keep the premise under 2,000 characters.')
                    # Rebuild an auto-seed rather than retaining a previously
                    # generated narrative when the creator changes the premise.
                    previous = json.loads(self.show_path.read_text())
                    if previous.get('version') == 3:
                        previous.setdefault('overrides', {})['premise'] = premise.strip()
                    else:
                        reference = previous['narrative']['characters'][0]['reference']
                        previous = dict(version=3, model=MODEL, mode='auto',
                                        overrides={'premise': premise.strip()}, images=[dict(reference=reference)])
                    self.show_path.write_text(json.dumps(previous))
            if self.monitor:
                await self.monitor
            if self.timer:
                self.timer.cancel()
            self.session_id = secrets.token_hex(12)
            self.run_dir = self.directory / 'runs' / self.session_id
            self.run_dir.mkdir(parents=True, mode=0o700)
            self.source, self.duration = source, duration
            self.logs, self.events = [], []
            self.counts = dict(messagesReceived=0, accepted=0, ignored=0, deferred=0)
            self.last_accepted = 0.0
            command = test_signal_command(self.run_dir, duration)
            worker_preset = self.show_path
            if source == 'fast_h3':
                fast_show = dict(version=1, model=FAST_MODEL,
                                 style='Natural motion, clear subjects, coherent camera and sound.',
                                 narrative={'premise': premise.strip()})
                if opening_image:
                    destination = self.run_dir / 'references' / ('character-1' + opening_image.suffix)
                    destination.parent.mkdir(mode=0o700)
                    shutil.copyfile(opening_image, destination)
                    fast_show['starting_frame'] = str(destination.relative_to(self.run_dir))
                worker_preset = self.run_dir / 'fast-show.json'
                worker_preset.write_text(json.dumps(fast_show))
            if source in ('h3', 'fast_h3'):
                command = [sys.executable, '-u', str(ROOT / 'runtime/main.py'),
                           '--preset', str(worker_preset), '--sink', 'preview']
            self.phase, self.started_at, self.ended_at = 'starting', time.time(), None
            self.log('Preview started. Runs until stopped.' if duration is None else 'Preview started. Auto-stop in %s seconds.' % duration)
            self.log('Synthetic test signal; no AI/API calls.' if source == 'test_signal'
                     else f'{"FastH3" if source == "fast_h3" else "H3 Turbo Realtime"} + Grok 4.7 API calls enabled. Output stays local.')
            try:
                self.process = await asyncio.create_subprocess_exec(
                    *command, cwd=ROOT, env=self.child_env(), stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT, start_new_session=True)
            except OSError:
                self.phase, self.ended_at = 'failed', time.time()
                self.log('Could not start the local encoder.', 'error')
                raise ValueError('Could not start the local encoder. Check FFmpeg installation.') from None
            self.monitor = asyncio.create_task(self.watch(self.process))
            self.timer = asyncio.create_task(self.auto_stop(self.process, duration)) if duration is not None else None
            return self.status()

    async def watch(self, process):
        async def drain():
            pending = b''
            while chunk := await process.stdout.read(4096):
                pending += chunk
                while b'\n' in pending or len(pending) > 8192:
                    if b'\n' in pending:
                        line, pending = pending.split(b'\n', 1)
                    else:
                        line, pending = pending[:8192], pending[8192:]
                    self.log(line.decode('utf-8', 'replace'))
            if pending:
                self.log(pending.decode('utf-8', 'replace'))
        reader = asyncio.create_task(drain())
        while process.returncode is None:
            segments = self.segments()  # Prune old preview clips even without a browser polling.
            if self.phase == 'starting' and segments:
                self.phase = 'running'
            await asyncio.sleep(.2)
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGTERM)
        try:
            await asyncio.wait_for(asyncio.shield(reader), 3)
        except asyncio.TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
            reader.cancel()
            await asyncio.gather(reader, return_exceptions=True)
        stopping = self.phase == 'stopping'
        self.phase = 'stopped' if stopping or process.returncode == 0 else 'failed'
        self.ended_at = time.time()
        await self.publisher.stop()
        self.log('Preview stopped; completed clips remain available.' if self.phase == 'stopped'
                 else 'Worker failed. Review the logs. Sending to X has stopped.',
                 'info' if self.phase == 'stopped' else 'error')

    async def auto_stop(self, process, duration):
        await asyncio.sleep(duration)
        if self.process is process and process.returncode is None:
            self.log('Rehearsal time limit reached. Stopping generation and encoding.')
            await self.stop()

    async def stop(self):
        async with self.lock:
            await self.publisher.stop()
            if self.timer and self.timer is not asyncio.current_task():
                self.timer.cancel()
            if self.process and self.monitor and not self.monitor.done():
                self.phase = 'stopping'
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(self.process.pid, signal.SIGTERM)
                try:
                    await asyncio.wait_for(asyncio.shield(self.monitor), 8)
                except asyncio.TimeoutError:
                    with contextlib.suppress(ProcessLookupError):
                        os.killpg(self.process.pid, signal.SIGKILL)
                    await asyncio.wait_for(asyncio.shield(self.monitor), 5)
            if self.phase != 'failed':
                self.phase = 'stopped'
            return self.status()

    def chat(self, text):
        if not isinstance(text, str) or not text.strip() or len(text) > 500:
            raise ValueError('Enter a chat message of 1–500 characters.')
        if self.phase not in ('starting', 'running'):
            raise ValueError('Start a rehearsal before testing chat.')
        self.counts['messagesReceived'] += 1
        suggestion = re.search(r'\b(make|change|add|turn|bring|let|move)\b', text, re.I)
        decision, reason = 'ignored', 'Fixture: no suggestion keyword. Not an AI judgment.'
        if suggestion:
            if time.monotonic() - self.last_accepted < 10:
                decision, reason = 'deferred', 'Fixture: 10-second suggestion cooldown. Not queued for generation.'
            else:
                decision, reason = 'accepted', 'Fixture: suggestion keyword matched. Not applied to video.'
                self.last_accepted = time.monotonic()
        self.counts[decision] += 1
        self.events.append(dict(id=secrets.token_hex(8), text=text.strip(), decision=decision,
                                reason=reason, at=time.time(), engine='fixture', applied=False))
        self.events = self.events[-50:]
        return self.status()

    async def upload(self, payload, premise):
        async with self.lock:
            if self.process and self.process.returncode is None:
                raise ValueError('Stop rehearsal before changing the reference image.')
            if not isinstance(premise, str) or len(premise) > 2000:
                raise ValueError('Keep the premise under 2,000 characters.')
            from PIL import Image
            try:
                with Image.open(io.BytesIO(payload)) as image:
                    extension = {'PNG': 'png', 'JPEG': 'jpg', 'WEBP': 'webp'}.get(image.format)
                    if not extension or image.width * image.height > 25_000_000:
                        raise ValueError('Use PNG, JPEG or WebP under 25 megapixels.')
                    image.verify()
            except (OSError, Image.DecompressionBombError):
                raise ValueError('Upload a valid PNG, JPEG or WebP reference image.') from None
            folder = self.directory / 'shows' / secrets.token_hex(12)
            (folder / 'references').mkdir(parents=True, mode=0o700)
            reference = f'references/character-1.{extension}'
            (folder / reference).write_bytes(payload)
            show = dict(version=3, model=MODEL, mode='auto',
                        overrides={'premise': premise.strip()} if premise.strip() else {},
                        images=[dict(reference=reference)])
            self.show_path = folder / 'show.json'
            self.show_path.write_text(json.dumps(show))
            self.show = dict(name='Your reference-image show', characters=1)
            return self.status()


def create_app(directory=None, env=None):
    studio = Studio(directory or ROOT / '.local', env)

    @web.middleware
    async def boundary(request, handler):
        # Reject DNS rebinding and cross-origin mutations. Bind only to loopback.
        host = request.host.split(':')[0]
        origin = request.headers.get('Origin')
        if (host not in ('127.0.0.1', 'localhost')
                or request.headers.get('Sec-Fetch-Site') == 'cross-site'
                or (origin and origin != f'http://{request.host}')
                or (request.method == 'POST' and request.headers.get('X-Studio-Control') != '1')):
            return web.json_response({'error': 'Open the local studio directly to control it.'}, status=403)
        try:
            response = await handler(request)
        except (ValueError, json.JSONDecodeError) as error:
            response = web.json_response({'error': str(error)}, status=400)
        except web.HTTPException as error:
            response = web.json_response({'error': error.reason}, status=error.status)
        except Exception as error:
            studio.log(f'Request failed ({type(error).__name__}).', 'error')
            response = web.json_response({'error': 'Local request failed. Check dependencies and studio logs.'}, status=500)
        response.headers.update({'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
                                 'Referrer-Policy': 'no-referrer', 'X-Frame-Options': 'DENY',
                                 'Content-Security-Policy': "default-src 'self'; script-src 'self'; style-src 'self'; font-src 'self' data:; img-src 'self' blob:; media-src 'self' blob:; frame-ancestors 'none'; base-uri 'none'"})
        return response

    app = web.Application(middlewares=[boundary], client_max_size=MAX_IMAGE)
    app[CONTROLLER] = studio

    async def body(request):
        if request.content_length is None or request.content_length > 8192:
            raise ValueError('Send a small JSON object.')
        value = await request.json()
        if not isinstance(value, dict):
            raise ValueError('Expected a JSON object.')
        return value

    async def status(request): return web.json_response(studio.status())
    async def start(request):
        data = await body(request)
        return web.json_response(await studio.start(data.get('source', 'test_signal'), data.get('duration'), data.get('confirmPaid', False), data.get('premise'), data.get('useStartingFrame', False)))
    async def stop(request): return web.json_response(await studio.stop())
    async def chat(request): return web.json_response(studio.chat((await body(request)).get('text')))
    async def credentials(request): return web.json_response(await studio.save_keys(await body(request)))
    async def destination(request): return web.json_response(await studio.save_destination(await body(request)))
    async def send_to_x(request): return web.json_response(await studio.start_publishing((await body(request)).get('confirmSend', False)))
    async def stop_x(request): return web.json_response(await studio.stop_publishing())
    async def upload(request):
        payload = await request.read()
        if not payload:
            raise ValueError('Choose a reference image.')
        return web.json_response(await studio.upload(payload, unquote(request.headers.get('X-Show-Premise', ''))))
    async def asset(request):
        name = request.match_info.get('name', 'index.html')
        if name not in ('index.html', 'app.js', 'style.css'):
            raise web.HTTPNotFound()
        return web.FileResponse(ROOT / 'web' / name)
    async def media(request):
        if request.match_info['session'] != studio.session_id:
            raise web.HTTPNotFound()
        name = request.match_info['name']
        if name not in {s['name'] for s in studio.segments()}:
            raise web.HTTPNotFound()
        return web.FileResponse(studio.run_dir / name)
    async def cleanup(app): await studio.stop()
    app.add_routes([web.get('/', asset), web.get('/api/status', status), web.post('/api/start', start),
                    web.post('/api/credentials', credentials),
                    web.post('/api/x/destination', destination), web.post('/api/x/start', send_to_x),
                    web.post('/api/x/stop', stop_x),
                    web.post('/api/stop', stop), web.post('/api/chat', chat), web.post('/api/show', upload),
                    web.get('/media/{session}/{name}', media), web.get('/{name}', asset)])
    app.on_cleanup.append(cleanup)
    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8091)
    parser.add_argument('--no-browser', action='store_true')
    args = parser.parse_args()
    os.umask(0o077)
    env = {**os.environ, **{k: v for k, v in dotenv_values(ROOT / '.env').items() if v is not None}}
    app = create_app(env=env)
    if not args.no_browser:
        async def open_browser(app):
            asyncio.get_running_loop().call_later(1, webbrowser.open, f'http://127.0.0.1:{args.port}')
        app.on_startup.append(open_browser)
    print('Local dashboard. X transmission requires Send to X. Press Ctrl+C to stop all output.')
    web.run_app(app, host='127.0.0.1', port=args.port, access_log=None)


if __name__ == '__main__':
    main()
