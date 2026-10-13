import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "runtime"))

from playback_buffer import PlaybackBuffer


class PlaybackBufferTests(unittest.TestCase):
    def test_supported_choices_and_default(self):
        self.assertEqual(PlaybackBuffer().seconds, 16)
        for seconds in (0, 8, 16, 24, 40):
            self.assertEqual(PlaybackBuffer(seconds).seconds, seconds)

    def test_rejects_bool_nan_and_out_of_range(self):
        for seconds in (True, False, math.nan, math.inf, -1, 7, 41, "16", None):
            with self.subTest(seconds=seconds), self.assertRaises(ValueError):
                PlaybackBuffer(seconds)

    def test_only_ready_positive_finite_numeric_clips_count(self):
        buffer = PlaybackBuffer(16)
        clips = [
            {"ready": True, "seconds": 8},
            {"ready": True, "seconds": 8.0},
            {"ready": True, "seconds": 0},
            {"ready": True, "seconds": -1},
            {"ready": True, "seconds": math.nan},
            {"ready": True, "seconds": math.inf},
            {"ready": True, "seconds": "8"},
            {"ready": False, "seconds": 100},
            {"seconds": 100},
        ]
        self.assertTrue(buffer.update(clips, playing=False, capacity=3, clip_seconds=8))
        self.assertEqual(buffer.snapshot()["readySeconds"], 16.0)

    def test_startup_warm_and_snapshot(self):
        buffer = PlaybackBuffer(16)
        self.assertFalse(buffer.update([{"ready": True, "seconds": 8}], playing=False, capacity=3, clip_seconds=8))
        self.assertTrue(buffer.update([{"ready": True, "seconds": 8}, {"ready": True, "seconds": 8}], playing=False, capacity=3, clip_seconds=8))
        self.assertEqual(buffer.snapshot(), {"phase": "playing", "readySeconds": 16.0, "targetSeconds": 16.0, "underruns": 0})

    def test_sustain_and_last_clip_rebuffer(self):
        buffer = PlaybackBuffer(16)
        ready = [{"ready": True, "seconds": 8}, {"ready": True, "seconds": 8}]
        self.assertTrue(buffer.update(ready, playing=False, capacity=3, clip_seconds=8))
        self.assertTrue(buffer.update([{"ready": True, "seconds": 8}], playing=True, capacity=3, clip_seconds=8))
        self.assertFalse(buffer.update([], playing=True, capacity=3, clip_seconds=8))
        self.assertEqual(buffer.snapshot()["phase"], "playing")
        self.assertFalse(buffer.update([], playing=False, capacity=3, clip_seconds=8))
        self.assertEqual(buffer.snapshot()["phase"], "rebuffering")
        self.assertEqual(buffer.snapshot()["underruns"], 1)
        self.assertFalse(buffer.update([], playing=False, capacity=3, clip_seconds=8))
        self.assertEqual(buffer.snapshot()["underruns"], 1)

    def test_rebuffer_recovers_with_two_clips(self):
        buffer = PlaybackBuffer(16)
        ready = [{"ready": True, "seconds": 8}, {"ready": True, "seconds": 8}]
        buffer.update(ready, playing=False, capacity=3, clip_seconds=8)
        buffer.update([], playing=False, capacity=3, clip_seconds=8)
        self.assertFalse(buffer.update([{"ready": True, "seconds": 8}], playing=False, capacity=3, clip_seconds=8))
        self.assertTrue(buffer.update(ready, playing=False, capacity=3, clip_seconds=8))
        self.assertEqual(buffer.snapshot()["phase"], "playing")

    def test_capacity_one_clamps_goal_and_zero_bypasses(self):
        buffer = PlaybackBuffer(16)
        self.assertTrue(buffer.update([{"ready": True, "seconds": 8}], playing=False, capacity=1, clip_seconds=8))
        self.assertEqual(buffer.snapshot()["targetSeconds"], 8.0)
        self.assertTrue(PlaybackBuffer(0).update([], playing=False, capacity=0, clip_seconds=8))

    def test_reset_clears_underruns_and_warm_state(self):
        buffer = PlaybackBuffer(8)
        buffer.update([{"ready": True, "seconds": 8}], playing=False, capacity=1, clip_seconds=8)
        buffer.update([], playing=False, capacity=1, clip_seconds=8)
        self.assertEqual(buffer.snapshot()["underruns"], 1)
        buffer.reset()
        self.assertEqual(buffer.snapshot(), {"phase": "warming", "readySeconds": 0.0, "targetSeconds": 8.0, "underruns": 0})

    def test_delayed_generation_recovery_without_sleep_or_provider(self):
        buffer = PlaybackBuffer(16)
        arrivals = [
            [],
            [{"ready": False, "seconds": 8}],
            [{"ready": True, "seconds": 8}],
            [{"ready": True, "seconds": 8}, {"ready": True, "seconds": 8}],
        ]
        decisions = [buffer.update(clips, playing=False, capacity=3, clip_seconds=8) for clips in arrivals]
        self.assertEqual(decisions, [False, False, False, True])
        self.assertEqual(buffer.snapshot()["phase"], "playing")
        self.assertLessEqual(len(arrivals), 4)


if __name__ == "__main__":
    unittest.main()
