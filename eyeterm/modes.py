"""Own optional trackers while a foreground eye changes mode."""
from __future__ import annotations

from .animation import Idle
from .trackers import CameraTracker, Look
from .window_tracking import WindowMouseTracker


class ModeController:
    """Keep the camera open only while Face mode is explicitly selected."""

    def __init__(self, mode='idle', *, camera_index=0, invert_x=False,
                 camera_backend='auto', mouse_space='window', window_id=None):
        self.mode = 'idle'
        self.message = ''
        self._idle = Idle()
        self._camera = None
        self._mouse = None
        self._mouse_space = mouse_space
        self._window_id = window_id
        self._camera_index = camera_index
        self._invert_x = invert_x
        self._camera_backend = camera_backend
        self._healthy = True
        self._closed = False
        if not self.switch(mode):
            # A command explicitly requesting a native input must explain its
            # failure rather than starting a different mode silently.
            self.close()
            raise RuntimeError(self.message)

    def switch(self, mode: str) -> bool:
        if mode not in ('idle', 'mouse', 'camera'):
            raise ValueError('mode must be idle, mouse or camera')
        if self._closed:
            return False
        if mode == self.mode and self._healthy:
            self.message = ''
            return True
        camera = None
        try:
            if mode == 'camera':
                # A front camera's raw frame has the opposite horizontal
                # direction to the user's screen. Correct Face mode by default;
                # --invert-x reverses that choice without changing Mouse mode.
                camera = CameraTracker(self._camera_index, invert_x=not self._invert_x,
                                       backend=self._camera_backend)
            elif mode == 'mouse':
                # Validate before releasing a working camera, so an unavailable
                # Quartz input leaves the previous mode usable.
                if self._mouse is None:
                    self._mouse = WindowMouseTracker(self._mouse_space, self._window_id)
                self._mouse.target()
        except Exception as exc:
            self.message = f'ERROR selecting {mode}: {exc}'
            return False
        try:
            self._release_camera()
        except Exception as exc:
            self._failed(exc)
            if camera is not None:
                camera.close()
            return False
        self._camera = camera
        self.mode = mode
        self._healthy = True
        self.message = ''
        return True

    def target(self, now: float) -> Look:
        if self._closed or not self._healthy:
            return Look(0, 0)
        if self.mode == 'idle':
            return self._idle.sample(now)
        try:
            if self.mode == 'camera':
                return self._camera.target()
            desired = self._mouse.target()
            return Look(-desired.x, desired.y) if self._invert_x else desired
        except Exception as exc:
            self._failed(exc)
            return Look(0, 0)

    def status(self, debug: bool = False) -> str:
        if self._closed:
            return 'CLOSED'
        if not self._healthy:
            text = f'{self.mode}: ERROR'
        elif self.mode == 'camera':
            try:
                snapshot = self._camera.status()
                labels = {'starting': 'STARTING', 'tracking': 'FACE FOUND',
                          'no-face': 'NO FACE', 'stalled': 'NO FRAMES',
                          'closed': 'CLOSED', 'error': 'ERROR'}
                text = f"camera: {labels.get(snapshot.state, 'STARTING')}"
                if debug:
                    text += (f' x={snapshot.look.x:+.2f} y={snapshot.look.y:+.2f}'
                             f' · {snapshot.backend}')
            except Exception as exc:
                self._failed(exc)
                text = 'camera: ERROR'
        else:
            text = ('auto: IDLE' if self.mode == 'idle'
                    else f'mouse: TRACKING · {self._mouse.status()}')
        return text + (f' · {self.message}' if self.message else '')

    def _release_camera(self):
        camera, self._camera = self._camera, None
        if camera is not None:
            camera.close()

    def _failed(self, exc: Exception):
        self._healthy = False
        self.message = f'ERROR in {self.mode}: {exc}. Select this mode to retry.'
        try:
            self._release_camera()
        except Exception as cleanup_error:
            self.message += f' Camera cleanup: {cleanup_error}'

    def close(self):
        if not self._closed:
            self._closed = True
            self._healthy = False
            try:
                self._release_camera()
            except Exception as exc:
                self.message = f'ERROR closing camera: {exc}'
            finally:
                if self._mouse is not None:
                    self._mouse.close()
