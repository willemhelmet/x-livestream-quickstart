import asyncio
import io
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time
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

    async def test_api_key_shortcuts_for_both_providers(self):
        html = await (await self.client.get('/')).text()
        for url, label in (('https://console.x.ai/team/default/api-keys','Get xAI key'),
                           ('https://www.reactor.inc/account/api-keys','Get Reactor key')):
            self.assertIn(f'href="{url}" target="_blank" rel="noopener noreferrer">{label}',html)
            self.assertEqual(html.count(f'href="{url}"'),1)

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

    async def test_model_status_reports_media_and_redacts_titles_without_exposing_file(self):
        self.studio.phase = 'running'
        self.studio.source = 'fast_h3'
        self.studio.run_dir = Path(self.tmp.name) / 'runs' / 'telemetry'
        self.studio.run_dir.mkdir(parents=True)
        path = self.studio.run_dir / 'model-status.json'
        value = dict(updatedAt=time.time(), connected=True, videoReceived=True, audioReceived=True,
                     currentScene='reactor-test-secret scene', currentPrompt='writer-test-secret camera', comingUp=['writer-test-secret next'],
                     generationQueued=1, playoutQueued=2, unexpected='private-field')
        path.write_text(json.dumps(value))
        model = self.studio.status()['model']
        self.assertTrue(model['connected'])
        self.assertTrue(model['audioReceived'])
        self.assertNotIn('reactor-test-secret', json.dumps(model))
        self.assertNotIn('writer-test-secret', json.dumps(model))
        self.assertNotIn('unexpected', model)
        self.assertEqual(model['currentPrompt'], '[redacted] camera')
        value['updatedAt'] -= 60
        path.write_text(json.dumps(value))
        self.assertFalse(self.studio.status()['model']['connected'])
        self.assertEqual((await self.client.get('/model-status.json')).status, 404)

    async def test_save_show_restores_settings_without_starting_generation(self):
        story = dict(premise='A living pottery studio', style='Warm light and quiet rain',
                     clipSeconds=10.125, continueScenes=True, useStartingFrame=False)
        response = await self.post('/api/story', story)
        self.assertEqual(response.status, 200)
        self.assertEqual((await response.json())['story'], story)
        self.assertIsNone(self.studio.process)
        self.assertEqual(self.studio.story_file.stat().st_mode & 0o777, 0o600)
        restored = Studio(Path(self.tmp.name), env={})
        self.assertEqual(restored.status()['story'], story)
        self.assertEqual((await self.post('/api/story', {**story, 'clipSeconds': 6})).status, 400)
        self.assertEqual(json.loads(self.studio.story_file.read_text()), story)
        self.assertEqual((await self.client.post('/api/story', json=story)).status, 403)

    async def test_buffer_settings_and_exact_dialogue_fail_before_generation(self):
        story = dict(premise='Fairy news',bufferSeconds=16,clipSeconds=8,creative=dict(dialogueMode='exact',
            dialogueLanguage='English',dialogueSpeaker='Lira',dialogueVoice='a clear low voice',exactDialogue='The enchanted bridge is open.'))
        saved = await self.post('/api/story',story)
        self.assertEqual(saved.status,200)
        self.assertEqual((await saved.json())['story']['bufferSeconds'],16)
        self.assertIsNone(self.studio.process)
        preview = await self.post('/api/prompt-preview',story)
        self.assertEqual(preview.status,200)
        self.assertIn('<d>[English] The enchanted bridge is open.</d>',(await preview.json())['prompt'])
        for buffer in (True,15,100):
            self.assertEqual((await self.post('/api/story',dict(story,bufferSeconds=buffer))).status,400)
        long_line = 'This is a very long sentence with too many words to fit a short clip.'
        self.assertEqual((await self.post('/api/start',dict(story,source='fast_h3',confirmPaid=True,creative={**story['creative'],'exactDialogue':long_line}))).status,400)
        self.assertIsNone(self.studio.process)

    async def test_x_waits_for_ready_buffer_and_grok_logo_is_served(self):
        self.studio.source = 'fast_h3'
        with patch.object(self.studio,'model_status',return_value={'buffer':{'phase':'warming'}}), patch.object(self.studio.publisher,'start') as start:
            response = await self.post('/api/x/start',{'confirmSend':True})
            self.assertEqual(response.status,400); start.assert_not_called()
        response = await self.client.get('/grok-logo.svg')
        self.assertEqual(response.status,200)
        self.assertIn('fill="white"',await response.text())
        html = await (await self.client.get('/')).text()
        self.assertIn('alt="Grok"',html); self.assertNotIn('class="x-logo"',html)

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

    async def test_creative_upload_save_restore_and_prompt_without_generation(self):
        image = io.BytesIO(); Image.new('RGB', (16,16), '#996644').save(image, format='PNG')
        result = await self.client.post('/api/assets', data=image.getvalue(), headers={**HEADERS,'X-Asset-Type':'image'})
        self.assertEqual(result.status, 200)
        asset = (await result.json())['uploadedAsset']
        story = dict(premise='A chef serves soup', creative=dict(camera='Slow push in',sound='Sizzling broth',
            images=[dict(id=asset['id'],kind='person',description='reactor-test-secret chef')], firstFrame=asset['id']))
        saved = await self.post('/api/story', story)
        self.assertEqual(saved.status, 200)
        self.assertNotIn('reactor-test-secret', json.dumps(await saved.json()))
        self.assertIsNone(self.studio.process)
        restored = Studio(Path(self.tmp.name), env={})
        self.assertEqual(restored.story['creative']['images'][0]['id'], asset['id'])
        self.assertEqual(restored.status()['assets'], [asset])
        prompt = await self.post('/api/prompt-preview', story)
        self.assertEqual(prompt.status, 200)
        self.assertIn('Slow push in', (await prompt.json())['prompt'])
        wrong = dict(story, creative={'images':[dict(id='0'*24,kind='person',description='Missing')]})
        self.assertEqual((await self.post('/api/story', wrong)).status, 400)
        self.assertEqual((await self.client.post('/api/assets', data=image.getvalue())).status, 403)
        self.assertEqual((await self.client.get('/assets/' + asset['id'] + '.png')).status, 404)
        media = await self.client.get('/api/assets/' + asset['id'])
        self.assertEqual(media.status, 200)
        self.assertEqual(await media.read(), image.getvalue())
        self.assertEqual((await self.client.get('/api/assets/' + '0'*24)).status, 404)
        self.assertEqual((self.studio.asset_dir / (asset['id']+'.png')).stat().st_mode & 0o777, 0o600)

    async def test_audio_upload_checks_decoded_duration_and_channels(self):
        import wave
        for seconds, expected in ((1,400),(3,200),(16,400)):
            buffer = io.BytesIO()
            with wave.open(buffer,'wb') as audio:
                audio.setnchannels(1); audio.setsampwidth(2); audio.setframerate(8000)
                audio.writeframes(b'\0\0' * 8000 * seconds)
            response = await self.client.post('/api/assets', data=buffer.getvalue(), headers={**HEADERS,'X-Asset-Type':'audio'})
            self.assertEqual(response.status, expected)
        self.assertEqual(len(self.studio.creative_assets()), 1)
        self.assertEqual(list(self.studio.asset_dir.glob('.audio-*')), [])

    async def test_operator_suggestion_is_queued_only_for_running_model(self):
        self.studio.phase = 'running'
        self.studio.source = 'fast_h3'
        self.studio.run_dir = Path(self.tmp.name) / 'runs' / 'test'
        self.studio.run_dir.mkdir(parents=True)
        inbox = self.studio.run_dir / 'chat-inbox.jsonl'
        inbox.touch()
        self.studio.process = type('Running', (), {'returncode': None})()
        response = await self.post('/api/chat', {'text': 'make it rain', 'author': 'viewer'})
        self.assertEqual(response.status, 200)
        event = (await response.json())['events'][-1]
        self.assertEqual(event['decision'], 'queued')
        self.assertEqual(event['engine'], 'operator')
        self.assertFalse(event['applied'])
        self.assertEqual(json.loads(inbox.read_text())['text'], 'make it rain')
        self.assertEqual((await self.post('/api/chat', {'text': 'change the scene'})).status, 400)
        self.assertEqual((await self.post('/api/chat', {'text': 'x' * 501})).status, 400)
        self.studio.source = 'test_signal'
        self.assertEqual((await self.post('/api/chat', {'text': 'make a cat'})).status, 400)
        self.studio.process = None

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
