"""Frame-rate independent movement and eyelid timing."""
from __future__ import annotations

import math
import random
import time

from .trackers import Look


def ease(t: float) -> float:
    """A bounded smoothstep with zero velocity at either end."""
    t = max(0.0, min(1.0, t))
    return t * t * (3.0 - 2.0 * t)


def _clamp(value: float) -> float:
    return max(-1.0, min(1.0, value)) if math.isfinite(value) else 0.0


class GazeSmoother:
    """Exponential gaze response; smoothing is a time constant in seconds."""

    def __init__(self, smoothing: float = .12):
        if not math.isfinite(smoothing) or smoothing < 0:
            raise ValueError('smoothing must be a finite nonnegative number of seconds')
        self.smoothing = smoothing
        self.last = Look(0, 0)

    def sample(self, desired: Look, dt: float) -> Look:
        if not math.isfinite(dt) or dt < 0:
            raise ValueError('elapsed time must be finite and nonnegative')
        alpha = 1.0 if self.smoothing == 0 else -math.expm1(-dt / self.smoothing)
        self.last = Look(
            self.last.x + (_clamp(desired.x) - self.last.x) * alpha,
            self.last.y + (_clamp(desired.y) - self.last.y) * alpha,
        )
        return self.last


class Idle:
    """Random fixations, smooth saccades and tiny independent eye movements."""

    def __init__(self):
        self.rng = random.Random(2026)
        self.last = time.monotonic()
        self.old = Look(0, 0)
        self.target = Look(0, 0)
        self.until = self.last
        self._duration = .2
        self._micro_rng = random.Random(101)
        self._micro_last = self.last
        self._micro_until = self.last
        self._micro_old = Look(0, 0)
        self._micro_target = Look(0, 0)

    def sample(self, t: float) -> Look:
        if not math.isfinite(t):
            raise ValueError('animation time must be finite')
        while t >= self.until:
            self.old = self.target
            self.target = Look(self.rng.uniform(-.82, .82), self.rng.uniform(-.60, .60))
            self.last = self.until
            self._duration = (self.rng.uniform(.055, .095) if self.rng.random() < .12
                              else self.rng.uniform(.16, .34))
            self.until = self.last + self._duration + self.rng.uniform(.7, 3.0)
        amount = ease((t - self.last) / self._duration)
        while t >= self._micro_until:
            self._micro_old = self._micro_target
            self._micro_target = Look(self._micro_rng.uniform(-.009, .009),
                                      self._micro_rng.uniform(-.007, .007))
            self._micro_last = self._micro_until
            self._micro_until = self._micro_last + self._micro_rng.uniform(.18, .55)
        micro = ease((t - self._micro_last) / (self._micro_until - self._micro_last))
        return Look(
            _clamp(self.old.x + (self.target.x - self.old.x) * amount
                   + self._micro_old.x + (self._micro_target.x - self._micro_old.x) * micro),
            _clamp(self.old.y + (self.target.y - self.old.y) * amount
                   + self._micro_old.y + (self._micro_target.y - self._micro_old.y) * micro),
        )


class Blink:
    """Irregular blinks: rapid closing, short contact, slower reopening."""

    def __init__(self):
        self.rng = random.Random(7321)
        self._next = time.monotonic() + self.rng.uniform(3.0, 7.0)
        self._start = self._next
        self._close = .08
        self._hold = .02
        self._open = .18
        self._depth = 1.0

    def sample(self, t: float) -> float:
        if not math.isfinite(t):
            raise ValueError('animation time must be finite')
        while t >= self._next:
            self._start = self._next
            self._close = self.rng.uniform(.065, .10)
            self._hold = self.rng.uniform(.015, .035)
            self._open = self.rng.uniform(.15, .23)
            self._depth = self.rng.uniform(.55, .75) if self.rng.random() < .14 else 1.0
            self._next = self._start + self.rng.uniform(3.0, 7.0)
        phase = t - self._start
        if phase < 0:
            return 1.0
        if phase < self._close:
            return 1.0 - self._depth * ease(phase / self._close)
        phase -= self._close
        if phase < self._hold:
            return 1.0 - self._depth
        return 1.0 - self._depth * (1.0 - ease((phase - self._hold) / self._open))
