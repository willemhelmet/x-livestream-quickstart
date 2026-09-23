"""Exercise the H3 raw-frame output path without calling the model."""
import asyncio
import csv
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'runtime'))
try:
    import numpy as np
    from sinks import AudioFormat, VideoFormat, make_sink
    from sinks.encoder import LocalPreviewSink
except ImportError:
    np = None


@unittest.skipUnless(np is not None and shutil.which('ffmpeg'), 'Full dependencies and FFmpeg required')
class EncoderTests(unittest.IsolatedAsyncioTestCase):
    async def test_raw_frames_and_audio_become_browser_mp4(self):
        with tempfile.TemporaryDirectory() as directory:
            sink = LocalPreviewSink(directory, output_width=320, output_height=180,
                                    output_fps=30, video_bitrate_k=500)
            try:
                await sink.start(VideoFormat(width=320, height=180, fps=24), AudioFormat(sample_rate=48000, channels=1))
                for n in range(24 * 6):
                    frame = np.zeros((180, 320, 3), dtype=np.uint8)
                    frame[:, :, 0] = (n * 3) % 255
                    sink.send_video(frame)
                    sink.send_audio(np.zeros(2000, dtype=np.int16))
                    await asyncio.sleep(1 / 24)
            finally:
                await sink.stop()
            manifest = Path(directory) / 'clips.csv'
            self.assertTrue(manifest.exists())
            rows = list(csv.reader(manifest.read_text().splitlines()))
            self.assertTrue(rows, list(sink._stderr_tail))
            clip = Path(directory) / rows[0][0]
            self.assertIn(b'ftyp', clip.read_bytes()[:32])
            if shutil.which('ffprobe'):
                result = subprocess.run(['ffprobe', '-v', 'error', '-show_streams', '-of', 'json', str(clip)],
                                        capture_output=True, check=True, text=True)
                streams = json.loads(result.stdout)['streams']
                self.assertEqual({s['codec_name'] for s in streams}, {'h264', 'aac'})

    async def test_network_destinations_and_live_sink_names_rejected(self):
        with self.assertRaises(ValueError):
            LocalPreviewSink('rtmps://example.invalid/live/secret')
        with self.assertRaises(ValueError):
            make_sink('rtmp', rtmp_url='rtmps://example.invalid/live/secret')


if __name__ == '__main__': unittest.main()
