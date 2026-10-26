"""Live mode ownership tested without opening native devices."""
import time

import pytest

from eyeterm.trackers import Look, TrackingStatus


def controller_module():
    try:
        from eyeterm import modes
    except ImportError:
        pytest.fail('Live ModeController has not been implemented')
    return modes


class Devices:
    """Camera/Quartz are external boundaries; lifetime is observable here."""

    def __init__(self):
        self.cameras = []
        self.camera_error = None
        self.mouse_error = None
        self.mouse = Look(.6, -.4)
        self.mouse_trackers = []

    def open_camera(self, index=0, *, invert_x=False, backend='auto'):
        if self.camera_error:
            raise self.camera_error
        assert not any(not camera.closed for camera in self.cameras), 'camera opened twice'
        camera = Camera(index, invert_x, backend)
        self.cameras.append(camera)
        return camera

    def read_mouse(self):
        if self.mouse_error:
            raise self.mouse_error
        return self.mouse

    def open_mouse(self, mouse_space='window', window_id=None):
        tracker = Mouse(self, mouse_space, window_id)
        self.mouse_trackers.append(tracker)
        return tracker


class Mouse:
    def __init__(self, devices, mouse_space, window_id):
        self.devices = devices
        self.options = (mouse_space, window_id)
        self.closed = False

    def target(self):
        assert not self.closed
        return self.devices.read_mouse()

    def status(self):
        space, window_id = self.options
        return 'screen' if space == 'screen' else f'window #{window_id or 11}'

    def close(self):
        self.closed = True


class Camera:
    def __init__(self, index, invert_x, backend):
        self.options = (index, invert_x, backend)
        self.closed = False
        self.close_count = 0
        self.error = None
        self.look = Look(-.3 if invert_x else .3, .2)
        self.snapshot = TrackingStatus('haar', 'tracking', self.look, .03)

    def target(self):
        assert not self.closed, 'closed camera was sampled'
        if self.error:
            raise self.error
        return self.look

    def status(self):
        assert not self.closed, 'closed camera was inspected'
        return self.snapshot

    def close(self):
        self.close_count += 1
        self.closed = True


def native_boundary(monkeypatch):
    modes = controller_module()
    devices = Devices()
    monkeypatch.setattr(modes, 'CameraTracker', devices.open_camera)
    monkeypatch.setattr(modes, 'WindowMouseTracker', devices.open_mouse, raising=False)
    def old_boundary():
        raise RuntimeError('mouse tracker boundary has not been connected')
    monkeypatch.setattr(modes, 'mouse_target', old_boundary, raising=False)
    return modes, devices


def test_auto_and_mouse_do_not_activate_camera(monkeypatch):
    modes, devices = native_boundary(monkeypatch)
    controller = modes.ModeController()
    now = time.monotonic()
    assert isinstance(controller.target(now), Look)
    assert controller.mode == 'idle'
    assert controller.switch('mouse')
    assert controller.target(now) == Look(.6, -.4)
    assert devices.cameras == []
    controller.close()


@pytest.mark.parametrize('initial', ['idle', 'camera'])
@pytest.mark.parametrize('invert,face_x,mouse_x', [(False, -.3, .6), (True, .3, -.6)])
def test_face_corrects_front_camera_direction_without_changing_mouse_direction(monkeypatch, initial, invert, face_x, mouse_x):
    modes, devices = native_boundary(monkeypatch)
    controller = modes.ModeController(initial, invert_x=invert)
    # The external detector sees the face at +.3 in the unmirrored camera
    # frame. Its on-screen direction is -.3; --invert-x reverses that default.
    try:
        assert controller.switch('camera')
        assert controller.target(0) == Look(face_x, .2)
        assert controller.switch('mouse')
        assert controller.target(0) == Look(mouse_x, -.4)
        assert controller.switch('camera')
        assert controller.target(0) == Look(face_x, .2)
    finally:
        controller.close()


@pytest.mark.parametrize('initial', ['idle', 'mouse', 'camera'])
@pytest.mark.parametrize('destination', ['idle', 'mouse', 'camera'])
def test_every_mode_pair_switches_without_duplicate_camera(monkeypatch, initial, destination):
    modes, devices = native_boundary(monkeypatch)
    controller = modes.ModeController(initial)
    original = devices.cameras[0] if devices.cameras else None
    assert controller.switch(destination)
    assert controller.mode == destination
    assert isinstance(controller.target(time.monotonic()), Look)
    assert sum(not camera.closed for camera in devices.cameras) == int(destination == 'camera')
    if original:
        assert original.closed == (destination != 'camera')
        assert len(devices.cameras) == 1
    controller.close()
    controller.close()
    assert all(camera.close_count == 1 for camera in devices.cameras)


def test_explicit_camera_passes_device_settings_and_mouse_inverts_once(monkeypatch):
    modes, devices = native_boundary(monkeypatch)
    controller = modes.ModeController('camera', camera_index=2, invert_x=True, camera_backend='haar')
    assert devices.cameras[0].options == (2, False, 'haar')
    assert controller.target(0) == Look(.3, .2)
    assert controller.switch('mouse')
    assert controller.target(0) == Look(-.6, -.4)
    controller.close()


@pytest.mark.parametrize('space,window_id,label', [('window', 23, 'window #23'),
                                                 ('screen', None, 'screen')])
def test_mouse_uses_configured_space_and_keeps_binding_across_mode_switches(monkeypatch, space, window_id, label):
    modes, devices = native_boundary(monkeypatch)
    controller = modes.ModeController('mouse', mouse_space=space, window_id=window_id,
                                      invert_x=True)
    assert controller.target(0) == Look(-.6, -.4)
    assert label in controller.status()
    assert controller.switch('idle')
    assert controller.switch('mouse')
    assert len(devices.mouse_trackers) == 1
    assert controller.target(0) == Look(-.6, -.4)
    controller.close()
    assert devices.mouse_trackers[0].closed


def test_mouse_default_window_space_and_visible_screen_fallback(monkeypatch):
    modes, devices = native_boundary(monkeypatch)
    controller = modes.ModeController('mouse')
    tracker = devices.mouse_trackers[0]
    assert tracker.options == ('window', None)
    tracker.status = lambda: 'screen fallback (own window unavailable)'
    assert 'screen fallback' in controller.status()
    assert controller.target(0) == Look(.6, -.4)
    controller.close()


@pytest.mark.parametrize('initial', ['idle', 'mouse'])
def test_failed_face_switch_preserves_working_mode_and_displays_error(monkeypatch, initial):
    modes, devices = native_boundary(monkeypatch)
    controller = modes.ModeController(initial)
    devices.camera_error = RuntimeError('Camera permission denied')
    assert not controller.switch('camera')
    assert controller.mode == initial
    assert 'Camera permission denied' in controller.message
    assert 'ERROR' in controller.status()
    assert 'Camera permission denied' in controller.status()
    if initial == 'mouse':
        assert controller.target(0) == Look(.6, -.4)
    else:
        assert isinstance(controller.target(time.monotonic()), Look)
    devices.camera_error = None
    assert controller.switch('camera')
    assert controller.mode == 'camera'
    assert controller.message == ''
    controller.close()


def test_failed_mouse_switch_keeps_existing_camera_open(monkeypatch):
    modes, devices = native_boundary(monkeypatch)
    controller = modes.ModeController('camera')
    camera = devices.cameras[0]
    devices.mouse_error = RuntimeError('Quartz cannot read cursor')
    assert not controller.switch('mouse')
    assert controller.mode == 'camera'
    assert controller.target(0) == Look(-.3, .2)
    assert not camera.closed
    assert 'Quartz' in controller.status()
    devices.mouse_error = None
    assert controller.switch('mouse')
    assert camera.closed
    assert controller.message == ''
    controller.close()


@pytest.mark.parametrize('initial', ['mouse', 'camera'])
def test_explicit_initial_native_failure_stays_actionable(monkeypatch, initial):
    modes, devices = native_boundary(monkeypatch)
    devices.camera_error = RuntimeError('Permission missing')
    devices.mouse_error = RuntimeError('Permission missing')
    with pytest.raises(RuntimeError, match='Permission missing'):
        modes.ModeController(initial)
    assert all(tracker.closed for tracker in devices.mouse_trackers)


@pytest.mark.parametrize('initial', ['mouse', 'camera'])
def test_runtime_failure_centers_without_silent_mode_change_and_allows_retry(monkeypatch, initial):
    modes, devices = native_boundary(monkeypatch)
    controller = modes.ModeController(initial)
    if initial == 'camera':
        camera = devices.cameras[0]
        camera.error = OSError('Device disconnected')
    else:
        devices.mouse_error = OSError('Device disconnected')
    assert controller.target(0) == Look(0, 0)
    assert controller.mode == initial
    assert 'Device disconnected' in controller.message
    assert 'ERROR' in controller.status()
    assert controller.target(0) == Look(0, 0)
    if initial == 'camera':
        assert camera.close_count == 1
    devices.mouse_error = None
    assert controller.switch(initial)
    assert controller.message == ''
    assert controller.target(0) == (Look(-.3, .2) if initial == 'camera' else Look(.6, -.4))
    controller.close()
    assert all(camera.close_count == 1 for camera in devices.cameras)


@pytest.mark.parametrize('state,label', [('tracking', 'FACE FOUND'), ('no-face', 'NO FACE'),
                                       ('starting', 'STARTING'), ('stalled', 'NO FRAMES')])
def test_face_presence_status_does_not_infer_detection_from_centered_gaze(monkeypatch, state, label):
    modes, devices = native_boundary(monkeypatch)
    controller = modes.ModeController('camera')
    camera = devices.cameras[0]
    camera.snapshot = TrackingStatus('haar', state, Look(0, 0), .03)
    assert label in controller.status()
    assert 'x=' not in controller.status()
    assert 'x=+0.00 y=+0.00' in controller.status(debug=True)
    assert 'haar' in controller.status(debug=True)
    controller.close()


def test_status_failure_releases_camera_and_keeps_controls_usable(monkeypatch):
    modes, devices = native_boundary(monkeypatch)
    controller = modes.ModeController('camera')
    camera = devices.cameras[0]
    def broken_status():
        raise OSError('Camera state unreadable')
    camera.status = broken_status
    assert 'ERROR' in controller.status()
    assert 'Camera state unreadable' in controller.message
    assert camera.closed
    assert controller.mode == 'camera'
    assert controller.switch('idle')
    assert controller.message == ''
    assert isinstance(controller.target(time.monotonic()), Look)
    controller.close()


def test_close_prevents_sampling_or_reopening_devices(monkeypatch):
    modes, devices = native_boundary(monkeypatch)
    controller = modes.ModeController('camera')
    controller.close()
    controller.close()
    assert controller.target(0) == Look(0, 0)
    assert 'CLOSED' in controller.status()
    assert not controller.switch('camera')
    assert len(devices.cameras) == 1
    assert devices.cameras[0].close_count == 1


def test_unknown_mode_does_not_disrupt_current_camera(monkeypatch):
    modes, devices = native_boundary(monkeypatch)
    controller = modes.ModeController('camera')
    with pytest.raises(ValueError, match='mode'):
        controller.switch('unknown')
    assert controller.mode == 'camera'
    assert not devices.cameras[0].closed
    assert controller.target(0) == Look(-.3, .2)
    controller.close()
