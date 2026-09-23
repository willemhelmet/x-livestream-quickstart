import json
import os
from pathlib import Path
import stat
import tempfile
import unittest

from aiohttp.test_utils import TestClient, TestServer

from server import CONTROLLER, Studio, create_app


HEADERS = {'X-Studio-Control': '1'}


class CredentialTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = {
            'PATH': os.environ.get('PATH', ''),
            'REACTOR_API_KEY': 'reactor-from-env',
            'XAI_API_KEY': 'xai-from-env',
            'OPENAI_API_KEY': 'must-not-escape',
        }
        self.app = create_app(Path(self.tmp.name), env=self.env)
        self.studio = self.app[CONTROLLER]
        self.client = TestClient(TestServer(self.app))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        self.tmp.cleanup()

    async def post(self, body, headers=None):
        return await self.client.post('/api/credentials', json=body,
                                      headers=HEADERS if headers is None else headers)

    async def test_save_maps_allowed_keys_atomically_and_redacts_response_logs(self):
        secret = 'new-reactor-secret'
        response = await self.post({'REACTOR_API_KEY': secret})
        self.assertEqual(response.status, 200)
        payload = await response.json()
        self.assertEqual(payload['credentials'], {'REACTOR_API_KEY': True, 'XAI_API_KEY': True})
        self.assertTrue(all(isinstance(value, bool) for value in payload['credentials'].values()))
        key_file = Path(self.tmp.name) / 'credentials.json'
        self.assertEqual(json.loads(key_file.read_text()), {'REACTOR_API_KEY': secret})
        self.assertEqual(stat.S_IMODE(key_file.stat().st_mode), 0o600)
        self.studio.log('Provider error mentioning ' + secret)
        self.assertTrue(all(secret not in json.dumps(item) for item in self.studio.logs))
        self.assertNotIn(secret, json.dumps(self.studio.status()))
        self.assertEqual(payload['writer']['model'], 'grok-4.7')

    async def test_empty_string_tombstone_overrides_environment_after_recreation(self):
        self.assertEqual((await self.post({'XAI_API_KEY': ''})).status, 200)
        recreated = Studio(Path(self.tmp.name), env=self.env)
        self.assertEqual(recreated.env['XAI_API_KEY'], '')
        self.assertFalse(recreated.status()['credentials']['XAI_API_KEY'])
        self.assertEqual(recreated.env['REACTOR_API_KEY'], 'reactor-from-env')

    async def test_invalid_updates_are_rejected_without_changing_file(self):
        self.assertEqual((await self.post({'REACTOR_API_KEY': 'keep-me'})).status, 200)
        key_file = Path(self.tmp.name) / 'credentials.json'
        original = key_file.read_bytes()
        for body in ({'UNKNOWN': 'x'}, {'OPENAI_API_KEY': 'x'}, {'REACTOR_API_KEY': 3}, {'REACTOR_API_KEY': ' has-space'},
                     {'REACTOR_API_KEY': 'x\n'}, {'REACTOR_API_KEY': 'x' * 2049}, []):
            self.assertEqual((await self.post(body)).status, 400)
            self.assertEqual(key_file.read_bytes(), original)

    async def test_guardrails_and_running_phase(self):
        for headers in ({}, {'Origin': 'https://evil.example', **HEADERS},
                        {'Sec-Fetch-Site': 'cross-site', **HEADERS}):
            self.assertEqual((await self.post({'XAI_API_KEY': 'x'}, headers)).status, 403)
        self.studio.phase = 'running'
        self.assertEqual((await self.post({'XAI_API_KEY': 'blocked'})).status, 400)
        self.assertFalse((Path(self.tmp.name) / 'credentials.json').exists())

    async def test_omitted_fields_unchanged_and_private_file_not_served(self):
        await self.post({'REACTOR_API_KEY': 'reactor-saved', 'XAI_API_KEY': 'xai-saved'})
        await self.post({'REACTOR_API_KEY': ''})
        self.assertEqual(json.loads((Path(self.tmp.name) / 'credentials.json').read_text()),
                         {'REACTOR_API_KEY': '', 'XAI_API_KEY': 'xai-saved'})
        self.assertEqual((await self.client.get('/credentials.json')).status, 404)
        self.assertEqual((await self.client.get('/.local/credentials.json')).status, 404)

    async def test_child_environment_excludes_openai_and_save_does_not_start_worker(self):
        before = self.studio.process
        await self.post({'REACTOR_API_KEY': 'local-only'})
        child = self.studio.child_env()
        self.assertNotIn('OPENAI_API_KEY', child)
        self.assertIs(self.studio.process, before)

    async def test_demo_does_not_accept_jev_credentials(self):
        response = await self.post({'JEV_API_KEY': 'unused-test-key'})
        self.assertEqual(response.status, 400)
        self.assertFalse(self.studio.key_file.exists())
        self.assertNotIn('JEV_API_KEY', self.studio.status()['credentials'])


if __name__ == '__main__':
    unittest.main()
