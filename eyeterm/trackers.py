"""Local optional inputs. No recording, event injection or network access."""
from __future__ import annotations

from dataclasses import dataclass
import math
import threading
import time


@dataclass(frozen=True)
class Look:
    x: float
    y: float


@dataclass(frozen=True)
class TrackingStatus:
    """An atomic camera snapshot, before display gain or gaze smoothing."""

    backend: str
    state: str
    look: Look
    age: float | None


def _bounded(x: float, y: float) -> Look:
    def clamp(value):
        return max(-1.0, min(1.0, value)) if math.isfinite(value) else 0.0
    return Look(clamp(x), clamp(y))


def mouse_target() -> Look:
    """Map the global cursor over the union of active CoreGraphics displays.

    CoreGraphics pointer and display coordinates share a top-left origin, so
    positive x/y means right/down, including displays with negative origins.
    """
    try:
        import Quartz
    except ImportError as exc:
        raise RuntimeError('Mouse support missing. Install: pip install -e ".[mouse]"') from exc
    try:
        event = Quartz.CGEventCreate(None)
        if event is None:
            raise RuntimeError('Mouse cursor could not be read from Quartz.')
        point = Quartz.CGEventGetLocation(event)
        error, displays, count = Quartz.CGGetActiveDisplayList(64, None, None)
        if error or not count:
            raise RuntimeError('Mouse tracking could not enumerate active displays.')
        bounds = [Quartz.CGDisplayBounds(display) for display in displays[:count]]
        bounds = [r for r in bounds if r.size.width > 0 and r.size.height > 0]
        if not bounds:
            raise RuntimeError('Mouse tracking found no usable display bounds.')
        left = min(r.origin.x for r in bounds)
        top = min(r.origin.y for r in bounds)
        right = max(r.origin.x + r.size.width for r in bounds)
        bottom = max(r.origin.y + r.size.height for r in bounds)
        return _bounded(2 * (point.x - left) / (right - left) - 1,
                        2 * (point.y - top) / (bottom - top) - 1)
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError('Mouse cursor could not be read through Quartz.') from exc


_CAMERA_HELP = ('Camera unavailable. Check macOS System Settings > Privacy & Security > Camera '
                'for Terminal.app / iTerm2, restart the terminal, or use --mode idle.')


class CameraTracker:
    """On-device face center, with capture/detection outside the render loop.

    ``auto`` uses an installed MediaPipe legacy FaceDetection solution (its
    models are package resources) and otherwise the OpenCV bundled Haar XML.
    Newer MediaPipe distributions without that API fall back without attempting
    model downloads. ``target`` never waits for capture or face detection.
    """

    def __init__(self, index: int = 0, invert_x: bool = False, backend: str = 'auto'):
        if backend not in ('auto', 'haar', 'mediapipe'):
            raise ValueError('camera backend must be auto, haar or mediapipe')
        if not isinstance(index, int) or index < 0:
            raise ValueError('camera index must be a nonnegative integer')
        try:
            import cv2
        except ImportError as exc:
            raise RuntimeError('Camera support missing. Install: pip install -e ".[camera]"; '
                               'or use --mode idle.') from exc
        self.cv2 = cv2
        self.invert_x = invert_x
        self.backend = backend
        self.cap = None
        self.detector = None
        self.last = Look(0, 0)
        self._started = time.monotonic()
        self._updated = None
        self._face_present = False
        self._error = None
        self._stop = threading.Event()
        self._state_lock = threading.Lock()
        self._release_lock = threading.Lock()
        self._released = False
        self._thread = None
        try:
            self.cap = cv2.VideoCapture(index)
            if not self.cap.isOpened():
                raise RuntimeError(_CAMERA_HELP)
            self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 320)
            self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 240)
            # Best effort: backends may ignore this, but must not buffer old gaze.
            self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            self.detector, self.backend = self._make_detector(backend)
            self._thread = threading.Thread(target=self._capture_loop,
                                            name='terminal-eye-camera', daemon=True)
            self._started = time.monotonic()
            self._thread.start()
        except BaseException as exc:
            self._release_capture()
            self._close_detector()
            if isinstance(exc, RuntimeError) or not isinstance(exc, Exception):
                raise
            raise RuntimeError(f'Camera initialization failed: {exc}. {_CAMERA_HELP}') from exc

    def _make_detector(self, backend):
        if backend != 'haar':
            try:
                # Check resources before importing. Tasks-only wheels have no
                # bundled legacy model and are deliberately not initialized.
                from importlib.metadata import PackageNotFoundError, distribution
                try:
                    package = distribution('mediapipe')
                except PackageNotFoundError:
                    package = None
                if package is not None:
                    required = ('mediapipe/python/solutions/face_detection.py',
                                'mediapipe/modules/face_detection/face_detection_short_range.tflite',
                                'mediapipe/modules/face_detection/face_detection_short_range_cpu.binarypb')
                    if not all(package.locate_file(path).is_file() for path in required):
                        raise RuntimeError('MediaPipe has no bundled legacy face detector resources.')
                import mediapipe as mp
                solution = mp.solutions.face_detection.FaceDetection
                return solution(model_selection=0, min_detection_confidence=.55), 'mediapipe'
            except Exception as exc:
                if backend == 'mediapipe':
                    raise RuntimeError('MediaPipe FaceDetection with local bundled models is unavailable. '
                                       'Use --camera-backend haar or install a compatible MediaPipe.') from exc
        cv2 = self.cv2
        detector = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_frontalface_default.xml')
        if detector.empty():
            raise RuntimeError('Camera face detector could not load the local OpenCV Haar cascade.')
        return detector, 'haar'

    def _detect(self, frame):
        return self._detect_face(frame)[0]

    def _detect_face(self, frame):
        """Return a target and presence separately: a centered face is valid."""
        cv2 = self.cv2
        if self.backend == 'mediapipe':
            result = self.detector.process(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
            boxes = [d.location_data.relative_bounding_box for d in (result.detections or [])]
            if not boxes:
                return Look(0, 0), False
            box = max(boxes, key=lambda b: b.width * b.height)
            x = 2 * (box.xmin + box.width / 2) - 1
            y = 2 * (box.ymin + box.height / 2) - 1
        else:
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            faces = self.detector.detectMultiScale(gray, scaleFactor=1.14,
                                                   minNeighbors=5, minSize=(35, 35))
            if not len(faces):
                return Look(0, 0), False
            x, y, width, height = max(faces, key=lambda f: f[2] * f[3])
            rows, cols = frame.shape[:2]
            x = 2 * (x + width / 2) / cols - 1
            y = 2 * (y + height / 2) / rows - 1
        return _bounded(-x if self.invert_x else x, y), True

    def _capture_loop(self):
        try:
            while not self._stop.is_set():
                started = time.monotonic()
                ok, frame = self.cap.read()
                if self._stop.is_set():
                    break
                if not ok or frame is None:
                    raise RuntimeError('Camera stopped returning frames. ' + _CAMERA_HELP)
                target, face_present = self._detect_face(frame)
                with self._state_lock:
                    self.last = target
                    self._face_present = face_present
                    self._updated = time.monotonic()
                # Detection is capped independently of terminal FPS/fast devices.
                self._stop.wait(max(0, 1 / 24 - (time.monotonic() - started)))
        except Exception as exc:
            with self._state_lock:
                self.last = Look(0, 0)
                self._error = RuntimeError(f'Camera capture or face detection failed: {exc}')
        finally:
            self._release_capture()
            self._close_detector()

    def target(self) -> Look:
        with self._state_lock:
            if self._error is not None:
                raise self._error
            # A driver stuck inside read() must not leave the eye fixated forever.
            if (self._stop.is_set() or self._updated is None
                    or time.monotonic() - self._updated > .75):
                return Look(0, 0)
            return self.last

    def status(self) -> TrackingStatus:
        """Return state without waiting for a frame or raising worker errors.

        Age measures time since the last completed detection (including a
        no-face result), and is None before any frame completes. A stalled
        snapshot retains the last raw target while target() safely centers.
        Explicit close takes precedence over an earlier worker error.
        """
        with self._state_lock:
            now = time.monotonic()
            age = None if self._updated is None else max(0.0, now - self._updated)
            elapsed = now - self._started if age is None else age
            if self._stop.is_set():
                state = 'closed'
            elif self._error is not None:
                state = 'error'
            elif elapsed > .75:
                state = 'stalled'
            elif age is None:
                state = 'starting'
            else:
                state = 'tracking' if self._face_present else 'no-face'
            return TrackingStatus(self.backend, state, self.last, age)

    def _release_capture(self):
        with self._release_lock:
            if self.cap is not None and not self._released:
                self._released = True
                try:
                    self.cap.release()
                except Exception:
                    # Preserve the original initialization/read error during cleanup.
                    pass

    def _close_detector(self):
        detector, self.detector = self.detector, None
        if detector is not None and hasattr(detector, 'close'):
            try:
                detector.close()
            except Exception:
                pass

    def close(self):
        self._stop.set()
        # AVFoundation/OpenCV release normally wakes read(). A wedged native
        # driver can still block in release; Python cannot cancel that C call.
        self._release_capture()
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=.8)
