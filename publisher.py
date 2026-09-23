"""Explicitly armed RTMPS relay for the local, completed preview segments."""
from __future__ import annotations

import asyncio
import contextlib
import re
import time
from urllib.parse import urlsplit


def validate_destination(value):
    if not isinstance(value, dict) or set(value) != {'serverUrl', 'streamKey'}:
        raise ValueError('Provide a server URL and stream key.')
    server, key = value['serverUrl'], value['streamKey']
    if not isinstance(server, str) or not isinstance(key, str):
        raise ValueError('Server URL and stream key must be text.')
    if not server and not key:
        return dict(serverUrl='', streamKey='')
    # Deliberately X-only: no arbitrary hosts, embedded credentials, or queries.
    if not re.fullmatch(r'rtmps://[a-z0-9-]+(?:\.[a-z0-9-]+)*\.pscp\.tv(?::443)?/x/?', server):
        raise ValueError('Paste the X RTMPS server URL (rtmps://…pscp.tv:443/x), without the stream key.')
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,512}', key):
        raise ValueError('Paste a valid X stream key, without spaces or a URL.')
    parsed = urlsplit(server)
    return dict(serverUrl=f'rtmps://{parsed.hostname}:443/x', streamKey=key)


def remux_command(path, offset):
    # Each independent MP4 starts near zero; offset onto one continuous timeline.
    return ['ffmpeg', '-hide_banner', '-loglevel', 'error', '-nostdin',
            '-protocol_whitelist', 'file,pipe', '-i', str(path),
            '-map', '0:v:0', '-map', '0:a:0', '-c', 'copy',
            '-bsf:v', 'h264_mp4toannexb', '-output_ts_offset', str(offset),
            '-mpegts_copyts', '1', '-muxdelay', '0', '-f', 'mpegts', 'pipe:1']


def publish_command(destination):
    destination = validate_destination(destination)
    if not destination['streamKey']:
        raise ValueError('Save your X destination first.')
    return ['ffmpeg', '-hide_banner', '-loglevel', 'error', '-nostdin',
            '-progress', 'pipe:1', '-stats_period', '1',
            '-re', '-f', 'mpegts', '-i', 'pipe:0',
            '-map', '0:v:0', '-map', '0:a:0', '-c', 'copy',
            '-tls_verify', '1', '-rw_timeout', '10000000',
            '-flvflags', 'no_duration_filesize', '-f', 'flv',
            destination['serverUrl'] + '/' + destination['streamKey']]


async def terminate(process):
    if process and process.returncode is None:
        with contextlib.suppress(ProcessLookupError):
            process.terminate()
        try:
            await asyncio.wait_for(process.wait(), 3)
        except asyncio.TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                process.kill()
            await process.wait()


class Publisher:
    def __init__(self):
        self.state = 'stopped'
        self.error = ''
        self.task = self.process = self.converter = None
        self._stop_lock = asyncio.Lock()

    @property
    def active(self):
        return self.task is not None and not self.task.done()

    def start(self, studio, destination):
        command = publish_command(destination)
        if self.active:
            raise ValueError('Already sending to X.')
        segments = studio.segments()
        if studio.phase != 'running' or not segments:
            raise ValueError('Start a preview and wait for video before sending to X.')
        self.state, self.error = 'connecting', ''
        self.task = asyncio.create_task(self._run(studio, command, segments[-1]['name']))

    async def _progress(self):
        while line := await self.process.stdout.readline():
            if line.startswith(b'out_time_us='):
                with contextlib.suppress(ValueError):
                    if int(line.split(b'=', 1)[1]) > 0:
                        self.state = 'sending'

    async def _run(self, studio, command, first):
        progress = None
        try:
            # Raw FFmpeg errors may contain the secret URL: never expose them.
            self.process = await asyncio.create_subprocess_exec(
                *command, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL)
            progress = asyncio.create_task(self._progress())
            last, offset, last_clip = None, 0.0, time.monotonic()
            connected_at = time.monotonic()
            while studio.phase in ('starting', 'running'):
                if self.process.returncode is not None:
                    raise RuntimeError('X connection closed. Check the destination and network, then try again.')
                if self.state == 'connecting' and time.monotonic() - connected_at > 25:
                    raise RuntimeError('X connection timed out. Check the destination and network.')
                pending = [s for s in studio.segments() if s['name'] >= first and (last is None or s['name'] > last)]
                if len(pending) > 8:
                    raise RuntimeError('Sending fell behind the preview. Reconnect to resume at the latest video.')
                if not pending:
                    if time.monotonic() - last_clip > 15:
                        raise RuntimeError('No new preview video. Sending to X stopped.')
                    await asyncio.sleep(.1)
                    continue
                segment = pending[0]
                self.converter = await asyncio.create_subprocess_exec(
                    *remux_command(studio.run_dir / segment['name'], offset),
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
                data, _ = await asyncio.wait_for(self.converter.communicate(), 10)
                if self.converter.returncode or not data:
                    raise RuntimeError('Could not prepare preview video for X.')
                self.process.stdin.write(data)
                await asyncio.wait_for(self.process.stdin.drain(), 15)
                last, offset, last_clip = segment['name'], offset + segment['duration'], time.monotonic()
            self.state = 'stopped'
        except asyncio.CancelledError:
            self.state = 'stopped'
            raise
        except Exception as error:
            self.state = 'failed'
            self.error = str(error) if isinstance(error, RuntimeError) else 'Could not send to X. Check your destination and network.'
        finally:
            if progress:
                progress.cancel()
                await asyncio.gather(progress, return_exceptions=True)
            await terminate(self.converter)
            if self.process and self.process.stdin:
                self.process.stdin.close()
            await terminate(self.process)

    async def stop(self):
        async with self._stop_lock:
            if self.active:
                self.task.cancel()
                await asyncio.gather(self.task, return_exceptions=True)
            self.state, self.error = 'stopped', ''
