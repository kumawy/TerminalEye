"""Observable timing and continuity contracts for the live eye."""
import math

import pytest

from eyeterm import animation
from eyeterm.trackers import Look


def test_idle_is_bounded_continuous_and_does_not_repeat_a_fixed_sweep(monkeypatch):
    monkeypatch.setattr(animation.time, 'monotonic', lambda: 0.0)
    idle = animation.Idle()
    points = [idle.sample(n / 1000) for n in range(20001)]
    assert points[0] == Look(0, 0)
    assert all(abs(p.x) <= 1 and abs(p.y) <= 1 for p in points)
    assert max(math.hypot(b.x - a.x, b.y - a.y) for a, b in zip(points, points[1:])) < .06
    # A live, pausing eye moves across multiple distinct targets, not a single sweep.
    seconds = [points[n * 1000] for n in range(20)]
    assert len({(round(p.x, 1), round(p.y, 1)) for p in seconds}) >= 6
    assert len({round(abs(b.x - a.x), 2) for a, b in zip(seconds, seconds[1:])}) >= 6


def test_blinks_are_irregular_close_quickly_and_reopen_more_slowly(monkeypatch):
    monkeypatch.setattr(animation.time, 'monotonic', lambda: 0.0)
    blink = animation.Blink()
    samples = [blink.sample(n / 1000) for n in range(60001)]
    assert samples[0] == 1
    assert min(samples) < .1
    assert all(0 <= sample <= 1 for sample in samples)
    runs = []
    start = None
    for index, value in enumerate(samples):
        if value < .999 and start is None:
            start = index
        if start is not None and value >= .999:
            runs.append((start, index))
            start = None
    intervals = [(b[0] - a[0]) / 1000 for a, b in zip(runs, runs[1:])]
    assert len(intervals) >= 7
    assert all(2.8 <= gap <= 7.2 for gap in intervals)
    assert len({round(gap, 1) for gap in intervals}) > 3
    for start, end in runs:
        trough = min(range(start, end), key=samples.__getitem__)
        assert trough - start < end - trough
        assert 100 <= end - start <= 450


def test_gaze_smoothing_is_frame_rate_independent_and_clamped():
    slow = animation.GazeSmoother(.12)
    fast = animation.GazeSmoother(.12)
    for _ in range(15):
        a = slow.sample(Look(2, -2), 1 / 15)
    for _ in range(60):
        b = fast.sample(Look(2, -2), 1 / 60)
    assert a.x == pytest.approx(b.x)
    assert a.y == pytest.approx(b.y)
    assert .99 < a.x <= 1
    assert -1 <= a.y < -.99
    assert animation.GazeSmoother(0).sample(Look(3, -4), .01) == Look(1, -1)


@pytest.mark.parametrize('bad', [-1, float('nan'), float('inf')])
def test_smoothing_rejects_invalid_time_constants(bad):
    with pytest.raises(ValueError):
        animation.GazeSmoother(bad)


@pytest.mark.parametrize('bad', [-1, float('nan'), float('inf')])
def test_smoothing_rejects_invalid_elapsed_time(bad):
    with pytest.raises(ValueError):
        animation.GazeSmoother().sample(Look(0, 0), bad)


def test_nonfinite_tracking_coordinates_return_to_center():
    assert animation.GazeSmoother(0).sample(Look(float('nan'), float('inf')), .1) == Look(0, 0)


def test_ease_clamps_the_opening_curve():
    assert [animation.ease(x) for x in [-1, 0, .5, 1, 2]] == [0, 0, .5, 1, 1]
