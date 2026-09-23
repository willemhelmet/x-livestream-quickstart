import asyncio
import io
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

from aiohttp.test_utils import TestClient, TestServer
from PIL import Image
from server import CONTROLLER, ROOT, Studio, create_app, test_signal_command

HEADERS = {'X-Studio-Control': '1'}


class StudioTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app = create_app(Path(self.tmp.name), env={
            'PATH': os.environ['PATH'], 'REACTOR_API_KEY': 'reactor-test-secret',
            'XAI_API_KEY': 'writer-test-secret', 'RTMP_URL': 'rtmps://example.invalid/LIVE-SECRET',
            'TWITCH_CHANNEL': 'never-connect', 'SETUP_PASSWORD': 'private-password'})
        self.studio = self.app[CONTROLLER]
        self.client = TestClient(TestServer(self.app))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        self.tmp.cleanup()

    async def post(self, path, value):
        return await self.client.post(path, json=value, headers=HEADERS)

    async def test_dashboard_copy_uses_grok_not_legacy_providers(self):
        for asset in ('/', '/app.js', '/style.css'):
            response = await self.client.get(asset)
            self.assertEqual(response.status, 200)
            text = await response.text()
            self.assertNotRegex(text.lower(), r'openai|chatgpt|codex')
            if asset == '/':
                self.assertIn('Livestream quickstart', text)
                self.assertIn('Live Preview', text)
                for removed in ('id="apiState"', 'id="sessionLabel"', 'Stop after', 'id="paidConsent"',
                                'Chat test', 'id="logList"', '<footer', 'Publishing disabled'):
                    self.assertNotIn(removed, text)

    async def test_cross_origin_rebinding_and_private_files(self):
        for headers in ({}, {**HEADERS, 'Origin': 'https://evil.example'},
                        {**HEADERS, 'Host': 'evil.example'}, {**HEADERS, 'Sec-Fetch-Site': 'cross-site'}):
            result = await self.client.post('/api/start', json={}, headers=headers)
            self.assertEqual(result.status, 403)
        for path in ('/.env', '/server.py', '/runtime/main.py', '/.local/', '/api/publish'):
            self.assertEqual((await self.client.get(path)).status, 404)
        self.assertEqual((await self.post('/api/publish', {})).status, 404)

    async def test_no_network_sink_even_with_live_env(self):
        response = await self.post('/api/start', {'source': 'rtmp'})
        self.assertEqual(response.status, 400)
        child = self.studio.child_env()
        for key in ('RTMP_URL', 'TWITCH_CHANNEL', 'SETUP_PASSWORD'):
            self.assertNotIn(key, child)
        self.assertEqual(child['SINK'], 'preview')
        command = test_signal_command(Path(self.tmp.name), 5)
        self.assertFalse(any('rtmp' in part for part in command))
        self.assertEqual(command[-2], 'file,pipe')
        self.studio.log('reactor-test-secret writer-test-secret rtmps://example.invalid/LIVE-SECRET')
        status = json.dumps(self.studio.status())
        for secret in ('reactor-test-secret', 'writer-test-secret', 'LIVE-SECRET', 'private-password'):
            self.assertNotIn(secret, status)

    async def test_invalid_requests_and_paid_gate(self):
        for body in ([], None, {'duration': True}, {'duration': 301}, {'duration': 0},
                     {'source': 'h3'}, {'source': 'h3', 'confirmPaid': 'true'},
                     {'source': 'h3', 'confirmPaid': True}):
            self.assertEqual((await self.post('/api/start', body)).status, 400)
        self.assertEqual((await self.post('/api/chat', {'text': 'make it rain'})).status, 400)
        self.assertIsNone(self.studio.process)

    async def test_image_upload_validates_actual_content(self):
        image = io.BytesIO()
        Image.new('RGB', (32, 32), '#996644').save(image, format='PNG')
        response = await self.client.post('/api/show', data=image.getvalue(), headers={**HEADERS, 'X-Show-Premise': 'A%20tiny%20cafe'})
        self.assertEqual(response.status, 200)
        self.assertEqual((await response.json())['show']['characters'], 1)
        saved = json.loads(self.studio.show_path.read_text())
        self.assertEqual(saved['overrides']['premise'], 'A tiny cafe')
        original = self.studio.show_path
        response = await self.client.post('/api/show', data=b'not an image', headers=HEADERS)
        self.assertEqual(response.status, 400)
        self.assertEqual(original, self.studio.show_path)

    async def test_fixture_decisions_are_explicit_and_bounded(self):
        self.studio.phase = 'running'
        for text, expected in [('love this', 'ignored'), ('make it rain', 'accepted'), ('change the scene', 'deferred')]:
            response = await self.post('/api/chat', {'text': text})
            event = (await response.json())['events'][-1]
            self.assertEqual(event['decision'], expected)
            self.assertEqual(event['engine'], 'fixture')
            self.assertFalse(event['applied'])
        self.assertEqual(self.studio.counts['messagesReceived'], 3)
        self.assertEqual((await self.post('/api/chat', {'text': 'x' * 501})).status, 400)

    async def test_paid_adapter_command_is_local_only_and_uses_latest_premise(self):
        image = io.BytesIO()
        Image.new('RGB', (16, 16)).save(image, format='PNG')
        await self.studio.upload(image.getvalue(), 'Original premise')
        with patch('server.asyncio.create_subprocess_exec', side_effect=OSError('do not run paid adapter')) as spawn:
            response = await self.post('/api/start', {'source': 'h3', 'duration': 60,
                                                      'confirmPaid': True, 'premise': 'Updated premise'})
            if not self.studio.status()['capabilities']['h3']:
                self.assertFalse(spawn.called)
                return
            self.assertEqual(response.status, 400)
            self.assertEqual(spawn.call_args.args[-2:], ('--sink', 'preview'))
            child = spawn.call_args.kwargs['env']
            self.assertNotIn('RTMP_URL', child)
            self.assertNotIn('TWITCH_CHANNEL', child)
            self.assertEqual(child['PYTHON_DOTENV_DISABLED'], '1')
            self.assertTrue(Path(child['PREVIEW_DIR']).is_dir())
            self.assertEqual(json.loads(self.studio.show_path.read_text())['overrides']['premise'], 'Updated premise')

    async def test_cleanup_kills_worker_group_after_parent_crash(self):
        command = [sys.executable, '-u', '-c',
                   "import subprocess,sys; subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); sys.exit(7)"]
        with patch('server.test_signal_command', return_value=command):
            await self.studio.start(duration=5)
        await asyncio.wait_for(self.studio.monitor, 6)
        self.assertEqual(self.studio.phase, 'failed')
        self.assertEqual(self.studio.process.returncode, 7)

    @unittest.skipUnless(shutil.which('ffmpeg'), 'FFmpeg is required for actual encoder acceptance')
    async def test_real_local_encoder_playback_stop_and_restart(self):
        # Fails if any synthetic path attempts to launch a model/API worker.
        response = await self.post('/api/start', {'source': 'test_signal', 'duration': 10})
        self.assertEqual(response.status, 200)
        first_session = self.studio.session_id
        pid = self.studio.process.pid
        self.assertEqual((await self.post('/api/start', {})).status, 400)
        self.assertEqual(pid, self.studio.process.pid)
        for _ in range(70):
            if self.studio.segments():
                break
            await asyncio.sleep(.1)
        segments = self.studio.segments()
        self.assertTrue(segments, self.studio.logs)
        response = await self.client.get(segments[0]['url'])
        self.assertEqual(response.status, 200)
        self.assertIn(b'ftyp', (await response.read())[:32])
        self.assertEqual((await self.client.get(f'/media/{first_session}/clips.csv')).status, 404)
        self.assertEqual((await self.client.get('/media/not-this-session/clip-000000.mp4')).status, 404)
        response = await self.post('/api/stop', {})
        self.assertEqual((await response.json())['state'], 'stopped')
        self.assertIsNotNone(self.studio.process.returncode)
        self.assertEqual((await self.client.get(segments[0]['url'])).status, 200)
        await self.post('/api/start', {'duration': 5})
        self.assertNotEqual(self.studio.session_id, first_session)
        self.assertEqual((await self.client.get(segments[0]['url'])).status, 404)
        await asyncio.wait_for(asyncio.shield(self.studio.monitor), 9)
        self.assertEqual(self.studio.phase, 'stopped')
        self.assertTrue(self.studio.segments())

    async def test_worker_spawn_failure_and_missing_ffmpeg(self):
        with patch('server.shutil.which', return_value=None):
            self.assertEqual((await self.post('/api/start', {})).status, 400)
        with patch('server.asyncio.create_subprocess_exec', side_effect=OSError('secret internal path')):
            response = await self.post('/api/start', {})
            self.assertEqual(response.status, 400)
            self.assertNotIn('secret internal path', await response.text())
        self.assertEqual(self.studio.phase, 'failed')


if __name__ == '__main__':
    unittest.main()
