import asyncio
import csv
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from publisher import Publisher, publish_command
from server import test_signal_command


@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'FFmpeg required')
class PublisherMediaTests(unittest.IsolatedAsyncioTestCase):
    async def test_multiple_preview_segments_form_decodable_continuous_flv(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            command = test_signal_command(directory, 6)
            command.remove('-re')
            source = await asyncio.create_subprocess_exec(*command, stderr=asyncio.subprocess.DEVNULL)
            self.assertEqual(await asyncio.wait_for(source.wait(), 15), 0)
            with (directory / 'clips.csv').open() as stream:
                segments = [dict(name=r[0], duration=float(r[2])-float(r[1])) for r in csv.reader(stream)]
            self.assertGreaterEqual(len(segments), 3)
            studio = SimpleNamespace(phase='running', run_dir=directory, segments=lambda: segments)
            publisher = Publisher()
            # Exercise the real RTMPS media pipeline, replacing ONLY its network
            # output with a temporary FLV file. No connection or credential used.
            command = publish_command(dict(serverUrl='rtmps://ca.pscp.tv:443/x', streamKey='test-only'))
            for flag in ('-tls_verify', '-rw_timeout'):
                index = command.index(flag)
                del command[index:index+2]
            output = directory / 'output.flv'
            command[-1] = str(output)
            publisher.state = 'connecting'
            publisher.task = asyncio.create_task(publisher._run(studio, command, segments[0]['name']))
            try:
                async with asyncio.timeout(15):
                    while publisher.state != 'sending':
                        self.assertNotEqual(publisher.state, 'failed', publisher.error)
                        await asyncio.sleep(.1)
                await asyncio.sleep(5)
            finally:
                await publisher.stop()
            self.assertFalse(publisher.active)
            self.assertIsNotNone(publisher.process.returncode)
            probe = await asyncio.create_subprocess_exec(
                'ffprobe', '-v', 'error', '-show_packets', '-select_streams', 'v',
                '-of', 'json', str(output), stdout=asyncio.subprocess.PIPE)
            payload, _ = await probe.communicate()
            self.assertEqual(probe.returncode, 0)
            packets = json.loads(payload)['packets']
            dts = [float(p['dts_time']) for p in packets]
            self.assertGreater(len(dts), 100)
            self.assertTrue(all(b > a for a, b in zip(dts, dts[1:])))
            self.assertGreater(dts[-1] - dts[0], 3.5)
            decoder = await asyncio.create_subprocess_exec(
                'ffmpeg', '-v', 'error', '-i', str(output), '-f', 'null', '-',
                stderr=asyncio.subprocess.PIPE)
            _, errors = await decoder.communicate()
            self.assertEqual(decoder.returncode, 0, errors.decode())
            self.assertEqual(errors, b'')

    async def test_failed_encoder_does_not_report_connected_or_expose_url(self):
        import sys
        publisher = Publisher()
        publisher.state = 'connecting'
        studio = SimpleNamespace(phase='running', segments=lambda: [])
        publisher.task = asyncio.create_task(publisher._run(
            studio, [sys.executable, '-c', 'import sys; sys.exit(1)'], 'clip-000001.mp4'))
        await asyncio.wait_for(publisher.task, 3)
        self.assertEqual(publisher.state, 'failed')
        self.assertIn('connection closed', publisher.error)
        self.assertNotIn('rtmps', publisher.error)
