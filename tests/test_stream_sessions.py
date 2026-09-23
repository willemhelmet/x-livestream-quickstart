import asyncio
import csv
import os
from pathlib import Path
import shutil
import tempfile
import time
import unittest
from unittest.mock import patch

from aiohttp.test_utils import TestClient, TestServer

from server import CONTROLLER, create_app, test_signal_command


HEADERS = {'X-Studio-Control': '1'}


class StreamSessionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app = create_app(Path(self.tmp.name), env={'PATH': os.environ['PATH']})
        self.studio = self.app[CONTROLLER]
        self.client = TestClient(TestServer(self.app))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.studio.stop()
        await self.client.close()
        self.tmp.cleanup()

    async def post(self, path, body):
        return await self.client.post(path, json=body, headers=HEADERS)

    def test_signal_commands_preserve_continuous_and_timed_modes(self):
        continuous = test_signal_command(Path(self.tmp.name), None)
        self.assertNotIn('-t', continuous)
        self.assertIn('-segment_list_size', continuous)
        self.assertEqual(continuous[continuous.index('-segment_list_size') + 1], '60')

        timed = test_signal_command(Path(self.tmp.name), 5)
        self.assertEqual(timed[timed.index('-t') + 1], '5')

    @unittest.skipUnless(shutil.which('ffmpeg'), 'FFmpeg is required for encoder acceptance')
    async def test_real_continuous_default_and_http_null_duration(self):
        # The default request is intentionally open-ended.  Use a monotonic
        # timestamp local to this test rather than Studio.started_at (epoch time).
        try:
            async with asyncio.timeout(12):
                started = time.monotonic()
                response = await self.post('/api/start', {})
                self.assertEqual(response.status, 200)
                payload = await response.json()
                self.assertIsNone(payload['autoStopSeconds'])
                self.assertIsNone(self.studio.duration)
                self.assertIsNone(self.studio.timer)

                while time.monotonic() - started < 6:
                    await asyncio.sleep(.1)
                self.assertIsNone(self.studio.process.returncode, self.studio.logs)

                response = await self.post('/api/stop', {})
                self.assertEqual(response.status, 200)
                self.assertEqual((await response.json())['state'], 'stopped')
                self.assertIsNotNone(self.studio.process.returncode)

                # JSON null follows the same continuous-default path over HTTP.
                response = await self.post('/api/start', {'duration': None})
                self.assertEqual(response.status, 200)
                self.assertIsNone((await response.json())['autoStopSeconds'])
                self.assertIsNone(self.studio.timer)
        finally:
            # Keep failures from leaving a real FFmpeg process behind.
            await self.studio.stop()

    async def test_h3_paid_gate_and_unknown_source_reject_before_spawn(self):
        with patch('server.shutil.which', return_value='/usr/bin/ffmpeg'), \
             patch('server.asyncio.create_subprocess_exec') as spawn:
            for body in ({'source': 'h3'}, {'source': 'fast_h3'}, {'source': 'unsupported'}):
                response = await self.post('/api/start', body)
                self.assertEqual(response.status, 400)
            spawn.assert_not_called()
        self.assertIsNone(self.studio.process)

    def test_retention_prunes_only_old_numeric_clips_in_current_run(self):
        run = Path(self.tmp.name) / 'runs' / 'current'
        outside = Path(self.tmp.name) / 'outside'
        run.mkdir(parents=True)
        outside.mkdir()
        self.studio.run_dir = run
        self.studio.session_id = 'current'

        rows = [(f'clip-{number:06d}.mp4', '0', '2') for number in range(60, 120)]
        with (run / 'clips.csv').open('w', newline='') as stream:
            csv.writer(stream).writerows(rows)
        for name, _, _ in rows:
            (run / name).write_bytes(b'closed clip')
        for number in (1, 59, 120):
            (run / f'clip-{number:06d}.mp4').write_bytes(b'local file')
        (run / 'unrelated.mp4').write_bytes(b'keep')
        (outside / 'clip-000001.mp4').write_bytes(b'outside')

        segments = self.studio.segments()
        self.assertEqual(len(segments), 60)
        self.assertEqual(segments[0]['name'], 'clip-000060.mp4')
        self.assertEqual(segments[-1]['name'], 'clip-000119.mp4')
        self.assertFalse((run / 'clip-000001.mp4').exists())
        self.assertFalse((run / 'clip-000059.mp4').exists())
        self.assertTrue((run / 'clip-000120.mp4').exists())
        self.assertTrue((run / 'unrelated.mp4').exists())
        self.assertTrue((outside / 'clip-000001.mp4').exists())


if __name__ == '__main__':
    unittest.main()
