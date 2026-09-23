import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import PropertyMock, patch

from aiohttp.test_utils import TestClient, TestServer

from publisher import publish_command, validate_destination
from server import CONTROLLER, Studio, create_app


HEADERS = {'X-Studio-Control': '1'}
DESTINATION = {'serverUrl': 'rtmps://live.pscp.tv:443/x', 'streamKey': 'key_123'}


class PublisherContractTests(unittest.TestCase):
    def test_destination_is_normalized_and_publish_uses_tls_verification(self):
        value = validate_destination({'serverUrl': 'rtmps://live.pscp.tv/x/', 'streamKey': 'key_123'})
        self.assertEqual(value, DESTINATION)
        command = publish_command(value)
        self.assertIn('-tls_verify', command)
        self.assertEqual(command[command.index('-tls_verify') + 1], '1')
        self.assertEqual(command[-1], DESTINATION['serverUrl'] + '/' + DESTINATION['streamKey'])

    def test_invalid_destinations_are_rejected(self):
        for server in ('', 'rtmp://live.pscp.tv/x', 'rtmps://evil.example/x',
                       'rtmps://live.pscp.tv/x?redirect=evil',
                       'rtmps://user:pass@live.pscp.tv/x'):
            with self.subTest(server=server):
                with self.assertRaises(ValueError):
                    validate_destination({'serverUrl': server, 'streamKey': 'key'})
        for key in ('', 'has space', 'line\nfeed', 'x' * 513):
            with self.subTest(key=key):
                with self.assertRaises(ValueError):
                    validate_destination({'serverUrl': DESTINATION['serverUrl'], 'streamKey': key})


class XDestinationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app = create_app(Path(self.tmp.name), env={'PATH': os.environ.get('PATH', '')})
        self.studio = self.app[CONTROLLER]
        self.client = TestClient(TestServer(self.app))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        self.tmp.cleanup()

    async def post(self, path, body, headers=None):
        return await self.client.post(path, json=body, headers=HEADERS if headers is None else headers)

    async def test_save_redacts_secret_and_survives_recreation(self):
        with patch('server.Publisher.start') as start:
            response = await self.post('/api/x/destination', DESTINATION)
        start.assert_not_called()
        self.assertIsNone(self.studio.publisher.task)
        self.assertEqual(response.status, 200)
        payload = await response.json()
        self.assertEqual(payload['x']['serverUrl'], DESTINATION['serverUrl'])
        self.assertTrue(payload['x']['hasStreamKey'])
        self.assertNotIn(DESTINATION['streamKey'], json.dumps(payload))
        saved = Path(self.tmp.name) / 'x-destination.json'
        self.assertEqual(json.loads(saved.read_text()), DESTINATION)
        self.assertEqual(stat.S_IMODE(saved.stat().st_mode), 0o600)
        recreated = Studio(Path(self.tmp.name), env={'PATH': os.environ.get('PATH', '')})
        self.assertEqual(recreated.status()['x']['serverUrl'], DESTINATION['serverUrl'])
        self.assertTrue(recreated.status()['x']['hasStreamKey'])
        self.assertIsNone(recreated.publisher.task)
        self.studio.log('relay key_123 failed at rtmps://live.pscp.tv:443/x/key_123')
        self.assertNotIn('key_123', json.dumps(self.studio.status()))
        for path in ('/x-destination.json', '/.local/x-destination.json'):
            self.assertEqual((await self.client.get(path)).status, 404)

    async def test_blank_key_preserves_old_key_and_both_blank_clears(self):
        await self.post('/api/x/destination', DESTINATION)
        response = await self.post('/api/x/destination', {'serverUrl': DESTINATION['serverUrl'], 'streamKey': ''})
        self.assertEqual(response.status, 200)
        self.assertNotIn('key_123', json.dumps(await response.json()))
        self.assertEqual(json.loads((Path(self.tmp.name) / 'x-destination.json').read_text()), DESTINATION)
        await self.post('/api/x/destination', {'serverUrl': '', 'streamKey': ''})
        self.assertEqual(json.loads((Path(self.tmp.name) / 'x-destination.json').read_text()), {'serverUrl': '', 'streamKey': ''})

    async def test_invalid_and_unguarded_mutations_are_rejected(self):
        saved = Path(self.tmp.name) / 'x-destination.json'
        for body in ({'serverUrl': 'rtmp://live.pscp.tv/x', 'streamKey': 'key'},
                     {'serverUrl': DESTINATION['serverUrl'] + '?x=1', 'streamKey': 'key'},
                     {'serverUrl': DESTINATION['serverUrl'], 'streamKey': 'bad key'}):
            self.assertEqual((await self.post('/api/x/destination', body)).status, 400)
            self.assertFalse(saved.exists())
        self.assertEqual((await self.post('/api/x/destination', DESTINATION, {})).status, 403)
        self.assertEqual((await self.post('/api/x/destination', DESTINATION, {'Origin': 'https://evil.example', **HEADERS})).status, 403)
        self.assertEqual((await self.post('/api/x/destination', DESTINATION, {'Sec-Fetch-Site': 'cross-site', **HEADERS})).status, 403)

    async def test_destination_cannot_change_while_publisher_active(self):
        await self.post('/api/x/destination', DESTINATION)
        with patch.object(type(self.studio.publisher), 'active', new_callable=PropertyMock, return_value=True):
            self.assertEqual((await self.post('/api/x/destination', {'serverUrl': DESTINATION['serverUrl'], 'streamKey': 'new_key'})).status, 400)

    async def test_all_x_mutations_require_same_origin_and_control_header(self):
        for path, body in (('/api/x/destination', DESTINATION), ('/api/x/start', {'confirmSend': True}), ('/api/x/stop', {})):
            for headers in ({}, {**HEADERS, 'Origin': 'https://evil.example'}, {**HEADERS, 'Sec-Fetch-Site': 'cross-site'}):
                with self.subTest(path=path, headers=headers):
                    self.assertEqual((await self.post(path, body, headers)).status, 403)

    async def test_start_requires_confirmation_and_completed_preview(self):
        await self.post('/api/x/destination', DESTINATION)
        for confirmation in (False, 'true', 1, None):
            self.assertEqual((await self.post('/api/x/start', {'confirmSend': confirmation})).status, 400)
        self.assertEqual((await self.post('/api/x/start', {'confirmSend': True})).status, 400)
        self.studio.phase = 'running'
        self.studio.segments = lambda: [{'name': 'clip-000001.mp4', 'duration': 2}]
        with patch('server.Publisher.start') as start:
            response = await self.post('/api/x/start', {'confirmSend': True})
        self.assertEqual(response.status, 200)
        start.assert_called_once()

    async def test_stop_returns_x_status_without_starting_anything(self):
        response = await self.post('/api/x/stop', {})
        self.assertEqual(response.status, 200)
        payload = await response.json()
        self.assertIn(payload['x']['state'], ('stopped', 'failed', 'connecting', 'sending'))
        self.assertNotIn('streamKey', json.dumps(payload))


if __name__ == '__main__':
    unittest.main()
