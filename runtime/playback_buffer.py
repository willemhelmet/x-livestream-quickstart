"""Bounded pre-roll/rebuffer decisions over finished media, not promised clips."""
import math

BUFFER_CHOICES = (0, 8, 16, 24, 40)
DEFAULT_BUFFER_SECONDS = 16


class PlaybackBuffer:
    def __init__(self, seconds=DEFAULT_BUFFER_SECONDS):
        if type(seconds) not in (int, float) or seconds not in BUFFER_CHOICES:
            raise ValueError('Choose a supported playback buffer.')
        self.seconds = seconds
        self.reset()

    def reset(self):
        self.phase = 'warming' if self.seconds else 'playing'
        self.has_started = False
        self.ready_seconds = 0.0
        self.target_seconds = float(self.seconds)
        self.underruns = 0

    def update(self, clips, *, playing, capacity, clip_seconds):
        self.ready_seconds = sum(float(clip['seconds']) for clip in clips
            if clip.get('ready') is True and type(clip.get('seconds')) in (int, float)
            and math.isfinite(clip['seconds']) and clip['seconds'] > 0)
        # A deployment with fewer ready slots must still be able to start.
        self.target_seconds = min(float(self.seconds), max(1, capacity) * clip_seconds)
        if not self.seconds:
            self.phase = 'playing'
            return True
        if self.phase in ('warming', 'rebuffering'):
            if self.ready_seconds + 0.001 < self.target_seconds:
                return False
            self.phase = 'playing'
            self.has_started = True
            return True
        if self.ready_seconds <= 0:
            # Disable autoplay while the last clip still plays: a late single
            # clip must not start before we rebuild a meaningful reserve.
            if not playing:
                self.phase = 'rebuffering' if self.has_started else 'warming'
                if self.has_started:
                    self.underruns += 1
            return False
        return True

    def snapshot(self):
        return dict(phase=self.phase, readySeconds=round(self.ready_seconds, 2),
                    targetSeconds=round(self.target_seconds, 2), underruns=self.underruns)
