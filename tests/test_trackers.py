"""Trackers with deterministic native/camera boundaries; no hardware is opened."""
from dataclasses import dataclass
import queue
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from eyeterm.trackers import CameraTracker, Look, mouse_target


def rect(x, y, width, height):
    return SimpleNamespace(origin=SimpleNamespace(x=x, y=y),
                           size=SimpleNamespace(width=width, height=height))


@pytest.mark.parametrize('point, expected', [((0, 0), Look(0, 0)),
                                            ((100, 50), Look(.5, .5)),
                                            ((-200, -100), Look(-1, -1))])
def test_mouse_normalizes_across_all_active_displays_including_negative_origins(monkeypatch, point, expected):
    bounds = {1: rect(0, 0, 200, 100), 2: rect(-200, -100, 200, 100)}
    monkeypatch.setitem(sys.modules, 'Quartz', SimpleNamespace(
        CGEventCreate=lambda _: object(),
        CGEventGetLocation=lambda _: SimpleNamespace(x=point[0], y=point[1]),
        CGGetActiveDisplayList=lambda *_: (0, [1, 2], 2),
        CGMainDisplayID=lambda: 1,
        CGDisplayBounds=bounds.__getitem__,
    ))
    assert mouse_target() == expected


def test_mouse_native_failure_is_actionable(monkeypatch):
    monkeypatch.setitem(sys.modules, 'Quartz', SimpleNamespace(
        CGEventCreate=lambda _: None, CGEventGetLocation=lambda _: None,
        CGGetActiveDisplayList=lambda *_: (0, [], 0),
        CGDisplayBounds=lambda _: None,
    ))
    with pytest.raises(RuntimeError, match='[Cc]ursor|[Mm]ouse|Quartz'):
        mouse_target()


@dataclass
class Frame:
    faces: tuple = ()
    shape: tuple = (100, 200, 3)


class Capture:
    """An external camera boundary: release unblocks a pending read."""
    def __init__(self, opened=True):
        self.opened = opened
        self.frames = queue.Queue()
        self.released = threading.Event()
        self.release_count = 0
        self.reading = threading.Event()

    def isOpened(self):
        return self.opened

    def set(self, *_):
        return True

    def read(self):
        self.reading.set()
        while not self.released.is_set():
            try:
                frame = self.frames.get(timeout=.01)
            except queue.Empty:
                continue
            if isinstance(frame, Exception):
                raise frame
            return frame is not None, frame
        return False, None

    def release(self):
        self.release_count += 1
        self.released.set()


class Detector:
    def empty(self):
        return False

    def detectMultiScale(self, frame, **_):
        return frame.faces


def camera_boundary(monkeypatch, cap=None, detector=None):
    cap = cap or Capture()
    detector = detector or Detector()
    monkeypatch.setitem(sys.modules, 'cv2', SimpleNamespace(
        VideoCapture=lambda _: cap,
        CascadeClassifier=lambda _: detector,
        data=SimpleNamespace(haarcascades='/local/opencv/'),
        CAP_PROP_FRAME_WIDTH=3, CAP_PROP_FRAME_HEIGHT=4, CAP_PROP_BUFFERSIZE=38,
        COLOR_BGR2GRAY=6, COLOR_BGR2RGB=4,
        cvtColor=lambda frame, _: frame,
    ))
    # An installed incompatible/new MediaPipe must not trigger network setup.
    monkeypatch.setitem(sys.modules, 'mediapipe', SimpleNamespace())
    return cap


def wait_for(call, condition, timeout=1):
    deadline = time.monotonic() + timeout
    while True:
        value = call()
        if condition(value):
            return value
        if time.monotonic() >= deadline:
            pytest.fail(f'tracker did not produce expected state, last value: {value!r}')
        time.sleep(.005)


@pytest.mark.parametrize('invert, expected', [(False, Look(.5, .2)), (True, Look(-.5, .2))])
def test_camera_tracks_largest_face_without_surprising_horizontal_mirroring(monkeypatch, invert, expected):
    cap = camera_boundary(monkeypatch)
    tracker = CameraTracker(invert_x=invert, backend='haar')
    try:
        cap.frames.put(Frame(((0, 0, 10, 10), (130, 40, 40, 40))))
        got = wait_for(tracker.target, lambda point: abs(point.x) > .1)
        assert got.x == pytest.approx(expected.x)
        assert got.y == pytest.approx(expected.y)
    finally:
        tracker.close()
    assert cap.release_count == 1


def test_camera_loss_returns_to_center_instead_of_retaining_last_face(monkeypatch):
    cap = camera_boundary(monkeypatch)
    tracker = CameraTracker(backend='haar')
    try:
        cap.frames.put(Frame(((130, 40, 40, 40),)))
        wait_for(tracker.target, lambda point: point.x > .1)
        cap.frames.put(Frame())
        assert wait_for(tracker.target, lambda point: point == Look(0, 0)) == Look(0, 0)
    finally:
        tracker.close()


def test_target_does_not_wait_for_a_camera_frame_and_close_releases_once(monkeypatch):
    cap = camera_boundary(monkeypatch)
    tracker = CameraTracker(backend='auto')
    try:
        assert cap.reading.wait(.5)
        begin = time.monotonic()
        assert tracker.target() == Look(0, 0)
        assert time.monotonic() - begin < .1
    finally:
        tracker.close()
        tracker.close()
    assert cap.release_count == 1


@pytest.mark.parametrize('failure', ['unopened', 'empty_detector', 'detector_init'])
def test_constructor_errors_release_the_camera(monkeypatch, failure):
    class BrokenDetector(Detector):
        def empty(self):
            return True
    cap = Capture(opened=failure != 'unopened')
    camera_boundary(monkeypatch, cap, BrokenDetector() if failure == 'empty_detector' else None)
    if failure == 'detector_init':
        def fail(_):
            raise ValueError('invalid local cascade')
        monkeypatch.setattr(sys.modules['cv2'], 'CascadeClassifier', fail)
    with pytest.raises(RuntimeError):
        CameraTracker(backend='haar')
    assert cap.release_count == 1


@pytest.mark.parametrize('failure', ['read', 'read_false', 'detect'])
def test_runtime_camera_errors_release_handle_and_report_failure(monkeypatch, failure):
    class BrokenDetector(Detector):
        def detectMultiScale(self, *_args, **_kwargs):
            raise ValueError('detector failed')
    cap = camera_boundary(monkeypatch, detector=BrokenDetector() if failure == 'detect' else None)
    tracker = CameraTracker(backend='haar')
    try:
        cap.frames.put(OSError('device disconnected') if failure == 'read'
                       else None if failure == 'read_false' else Frame())
        assert cap.released.wait(1), 'camera handle leaked on worker failure'
        with pytest.raises(RuntimeError, match='[Cc]amera|[Dd]etect'):
            tracker.target()
    finally:
        tracker.close()
    assert cap.release_count == 1


def test_auto_backend_uses_compatible_local_mediapipe_and_closes_it(monkeypatch):
    cap = camera_boundary(monkeypatch)
    closed = threading.Event()
    class FaceDetection:
        def __init__(self, **_):
            pass
        def process(self, _):
            box = SimpleNamespace(xmin=.65, ymin=.4, width=.2, height=.4)
            detection = SimpleNamespace(location_data=SimpleNamespace(relative_bounding_box=box))
            return SimpleNamespace(detections=[detection])
        def close(self):
            closed.set()
    monkeypatch.setitem(sys.modules, 'mediapipe', SimpleNamespace(
        solutions=SimpleNamespace(face_detection=SimpleNamespace(FaceDetection=FaceDetection))))
    tracker = CameraTracker(backend='auto')
    try:
        cap.frames.put(Frame())
        point = wait_for(tracker.target, lambda point: point.x > .1)
        assert point.x == pytest.approx(.5)
        assert point.y == pytest.approx(.2)
        assert tracker.backend == 'mediapipe'
    finally:
        tracker.close()
    assert closed.is_set()
    assert cap.release_count == 1


def test_explicit_unavailable_mediapipe_does_not_silently_change_backend(monkeypatch):
    cap = camera_boundary(monkeypatch)
    with pytest.raises(RuntimeError, match='MediaPipe'):
        CameraTracker(backend='mediapipe')
    # Depending on setup order, a handle may not have been opened at all.
    assert cap.release_count <= 1


def test_stalled_capture_returns_stale_face_to_center(monkeypatch):
    cap = camera_boundary(monkeypatch)
    tracker = CameraTracker(backend='haar')
    try:
        cap.frames.put(Frame(((130, 40, 40, 40),)))
        wait_for(tracker.target, lambda point: point.x > .1)
        # Only the clock advances: the capture worker is still blocked on read.
        future = time.monotonic() + 1
        monkeypatch.setattr('eyeterm.trackers.time.monotonic', lambda: future)
        assert tracker.target() == Look(0, 0)
    finally:
        tracker.close()


def test_auto_falls_back_when_local_mediapipe_cannot_initialize(monkeypatch):
    cap = camera_boundary(monkeypatch)
    def fail(**_):
        raise FileNotFoundError('bundled model is missing')
    monkeypatch.setitem(sys.modules, 'mediapipe', SimpleNamespace(
        solutions=SimpleNamespace(face_detection=SimpleNamespace(FaceDetection=fail))))
    tracker = CameraTracker(backend='auto')
    try:
        cap.frames.put(Frame(((130, 40, 40, 40),)))
        point = wait_for(tracker.target, lambda point: point.x > .1)
        assert point.x == pytest.approx(.5)
        assert tracker.backend == 'haar'
    finally:
        tracker.close()


def test_constructor_interruption_releases_handle(monkeypatch):
    cap = camera_boundary(monkeypatch)
    def interrupt(_):
        raise KeyboardInterrupt()
    monkeypatch.setattr(sys.modules['cv2'], 'CascadeClassifier', interrupt)
    with pytest.raises(KeyboardInterrupt):
        CameraTracker(backend='haar')
    assert cap.release_count == 1


def test_auto_does_not_import_modern_tasks_only_mediapipe(monkeypatch):
    """Modern Tasks need a separate model and can initialize telemetry."""
    import importlib.metadata
    from pathlib import Path
    cap = camera_boundary(monkeypatch)
    def should_not_construct(**_):
        pytest.fail('Tasks-only distribution must not initialize MediaPipe at runtime')
    monkeypatch.setitem(sys.modules, 'mediapipe', SimpleNamespace(
        solutions=SimpleNamespace(face_detection=SimpleNamespace(FaceDetection=should_not_construct))))
    monkeypatch.setattr(importlib.metadata, 'distribution',
                        lambda _: SimpleNamespace(locate_file=lambda path: Path('/missing') / path))
    tracker = CameraTracker(backend='auto')
    try:
        cap.frames.put(Frame(((130, 40, 40, 40),)))
        point = wait_for(tracker.target, lambda point: point.x > .1)
        assert point.x == pytest.approx(.5)
        assert tracker.backend == 'haar'
    finally:
        tracker.close()


def test_camera_status_distinguishes_a_centered_face_from_absence(monkeypatch):
    cap = camera_boundary(monkeypatch)
    tracker = CameraTracker(backend='haar')
    try:
        # Center is exactly (100, 50) in a 200x100 frame.
        cap.frames.put(Frame(((80, 30, 40, 40),)))
        centered = wait_for(tracker.status, lambda status: status.state == 'tracking')
        assert centered.backend == 'haar'
        assert centered.look == Look(0, 0)
        assert 0 <= centered.age < .5
        assert tracker.target() == Look(0, 0)
        cap.frames.put(Frame())
        absent = wait_for(tracker.status, lambda status: status.state == 'no-face')
        assert absent.look == Look(0, 0)
        assert 0 <= absent.age < .5
        # Snapshots describe their own frame rather than mutating in place.
        assert centered.state == 'tracking'
        from dataclasses import FrozenInstanceError
        with pytest.raises(FrozenInstanceError):
            centered.state = 'no-face'
    finally:
        tracker.close()


def test_camera_status_reports_startup_stall_without_inventing_frame_age(monkeypatch):
    cap = camera_boundary(monkeypatch)
    tracker = CameraTracker(backend='haar')
    try:
        assert cap.reading.wait(.5)
        starting = tracker.status()
        assert starting.backend == 'haar'
        assert starting.state == 'starting'
        assert starting.look == Look(0, 0)
        assert starting.age is None
        future = time.monotonic() + 2
        monkeypatch.setattr('eyeterm.trackers.time.monotonic', lambda: future)
        stalled = tracker.status()
        assert stalled.state == 'stalled'
        assert stalled.age is None
        assert stalled.look == Look(0, 0)
    finally:
        tracker.close()
    assert tracker.status().state == 'closed'
    assert tracker.status().age is None


def test_camera_status_retains_raw_gaze_but_target_centers_when_frames_stall(monkeypatch):
    cap = camera_boundary(monkeypatch)
    tracker = CameraTracker(backend='haar')
    try:
        cap.frames.put(Frame(((130, 40, 40, 40),)))
        recent = wait_for(tracker.status, lambda status: status.state == 'tracking')
        assert recent.look.x == pytest.approx(.5)
        assert recent.look.y == pytest.approx(.2)
        future = time.monotonic() + 2
        monkeypatch.setattr('eyeterm.trackers.time.monotonic', lambda: future)
        stale = tracker.status()
        assert stale.state == 'stalled'
        assert stale.look == recent.look
        assert stale.age >= 2
        assert tracker.target() == Look(0, 0)
    finally:
        tracker.close()
    assert tracker.status().state == 'closed'
    assert tracker.status().look == recent.look


def test_camera_status_reports_worker_error_without_raising(monkeypatch):
    cap = camera_boundary(monkeypatch)
    tracker = CameraTracker(backend='haar')
    try:
        cap.frames.put(OSError('disconnected'))
        assert cap.released.wait(1)
        status = tracker.status()
        assert status.state == 'error'
        assert status.backend == 'haar'
        assert status.look == Look(0, 0)
        assert status.age is None
        with pytest.raises(RuntimeError):
            tracker.target()
    finally:
        tracker.close()
    assert tracker.status().state == 'closed'


def test_mediapipe_status_reports_detected_centered_face(monkeypatch):
    cap = camera_boundary(monkeypatch)
    class FaceDetection:
        def __init__(self, **_):
            pass
        def process(self, frame):
            boxes = [SimpleNamespace(xmin=x, ymin=y, width=w, height=h)
                     for x, y, w, h in frame.faces]
            detections = [SimpleNamespace(location_data=SimpleNamespace(relative_bounding_box=box))
                          for box in boxes]
            return SimpleNamespace(detections=detections)
        def close(self):
            pass
    monkeypatch.setitem(sys.modules, 'mediapipe', SimpleNamespace(
        solutions=SimpleNamespace(face_detection=SimpleNamespace(FaceDetection=FaceDetection))))
    tracker = CameraTracker(backend='mediapipe')
    try:
        cap.frames.put(Frame(((.4, .3, .2, .4),)))
        status = wait_for(tracker.status, lambda status: status.state == 'tracking')
        assert status.look == Look(0, 0)
        assert status.backend == 'mediapipe'
        cap.frames.put(Frame())
        absent = wait_for(tracker.status, lambda status: status.state == 'no-face')
        assert absent.look == Look(0, 0)
    finally:
        tracker.close()
