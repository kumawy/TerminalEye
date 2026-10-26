"""Window mouse geometry with deterministic Quartz and process boundaries."""
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from eyeterm.trackers import Look


@pytest.fixture(autouse=True)
def no_inherited_tmux(monkeypatch):
    monkeypatch.delenv('TMUX', raising=False)
    monkeypatch.delenv('TMUX_PANE', raising=False)


def tracking_module():
    try:
        from eyeterm import window_tracking
    except ImportError:
        pytest.fail('Per-window mouse tracking has not been implemented')
    return window_tracking


def quartz_boundary(monkeypatch, windows=None, point=(100, 50)):
    state = SimpleNamespace(windows=windows or {}, point=point, queries=0, reads=0, now=0.)

    def list_windows(option, window_id):
        state.queries += 1
        if option == 8:
            return [state.windows[window_id]] if window_id in state.windows else []
        return list(state.windows.values())

    def location(_):
        state.reads += 1
        return SimpleNamespace(x=state.point[0], y=state.point[1])

    quartz = SimpleNamespace(
        CGEventCreate=lambda _: object(), CGEventGetLocation=location,
        CGWindowListCopyWindowInfo=list_windows,
        kCGWindowListOptionIncludingWindow=8, kCGWindowListOptionAll=0,
        kCGNullWindowID=0, kCGWindowNumber='kCGWindowNumber',
        kCGWindowBounds='kCGWindowBounds', kCGWindowOwnerName='kCGWindowOwnerName',
        CGGetActiveDisplayList=lambda *_: (0, [1], 1),
        CGDisplayBounds=lambda _: SimpleNamespace(origin=SimpleNamespace(x=0, y=0),
                                                  size=SimpleNamespace(width=800, height=400)),
    )
    monkeypatch.setitem(sys.modules, 'Quartz', quartz)
    return state


def window(window_id, left=0, top=0, width=200, height=100, owner='Terminal'):
    return {'kCGWindowNumber': window_id, 'kCGWindowOwnerName': owner,
            'kCGWindowBounds': {'X': left, 'Y': top, 'Width': width, 'Height': height}}


def test_same_global_pointer_looks_right_from_left_window_and_left_from_right(monkeypatch):
    tracking = tracking_module()
    quartz_boundary(monkeypatch, {11: window(11), 22: window(22, left=400)}, point=(300, 50))
    left = tracking.WindowMouseTracker(window_id=11)
    right = tracking.WindowMouseTracker(window_id=22)
    assert left.target() == Look(1, 0)
    assert right.target() == Look(-1, 0)
    assert 'window' in left.status()


@pytest.mark.parametrize('point,want', [((-200, -100), Look(0, 0)),
                                       ((-250, -125), Look(-.5, -.5)),
                                       ((-150, -75), Look(.5, .5))])
def test_window_coordinates_keep_quartz_vertical_sign_and_negative_origins(monkeypatch, point, want):
    tracking = tracking_module()
    quartz_boundary(monkeypatch, {11: window(11, -300, -150)}, point)
    assert tracking.WindowMouseTracker(window_id=11).target() == want


def test_pointer_is_sampled_every_frame_and_moved_window_bounds_refresh_at_four_hz(monkeypatch):
    tracking = tracking_module()
    native = quartz_boundary(monkeypatch, {11: window(11)}, point=(100, 50))
    monkeypatch.setattr(tracking.time, 'monotonic', lambda: native.now)
    tracker = tracking.WindowMouseTracker(window_id=11)
    assert tracker.target() == Look(0, 0)
    native.windows[11] = window(11, left=100)
    native.now = .1
    native.point = (150, 50)
    assert tracker.target() == Look(.5, 0)
    assert native.queries == 1
    native.now = .25
    assert tracker.target() == Look(-.5, 0)
    assert native.queries == 2
    assert native.reads == 3


@pytest.mark.parametrize('record', [None, window(11, width=0), window(11, width=float('nan'))])
def test_missing_or_invalid_window_bounds_visibly_fall_back_to_live_screen_tracking(monkeypatch, record):
    tracking = tracking_module()
    native = quartz_boundary(monkeypatch, {} if record is None else {11: record}, point=(600, 100))
    tracker = tracking.WindowMouseTracker(window_id=11)
    assert tracker.target() == Look(.5, -.5)
    assert 'screen fallback' in tracker.status()
    native.point = (200, 300)
    assert tracker.target() == Look(-.5, .5)


def test_disappeared_window_recovers_only_its_original_owner_and_binding(monkeypatch):
    tracking = tracking_module()
    native = quartz_boundary(monkeypatch, {11: window(11)}, point=(100, 50))
    monkeypatch.setattr(tracking.time, 'monotonic', lambda: native.now)
    tracker = tracking.WindowMouseTracker(window_id=11)
    assert tracker.target() == Look(0, 0)
    native.windows = {}
    native.now = .25
    assert tracker.target() == Look(-.75, -.75)
    assert 'screen fallback' in tracker.status()
    native.windows = {11: window(11, owner='Other App')}
    native.now = .5
    assert tracker.target() == Look(-.75, -.75)
    native.windows = {11: window(11, left=100)}
    native.now = .75
    assert tracker.target() == Look(-1, 0)
    assert tracker.status() == 'window #11'


def test_explicit_screen_space_never_resolves_or_enumerates_windows(monkeypatch):
    tracking = tracking_module()
    native = quartz_boundary(monkeypatch, {11: window(11)}, point=(600, 100))
    def forbidden(_):
        pytest.fail('screen tracking must not resolve a terminal')
    monkeypatch.setattr(tracking.WindowMouseTracker, '_resolve_window', forbidden)
    tracker = tracking.WindowMouseTracker(mouse_space='screen')
    assert tracker.target() == Look(.5, -.5)
    assert tracker.status() == 'screen'
    assert native.queries == 0


@pytest.mark.parametrize('window_id', [0, -1, True, '11'])
def test_explicit_window_id_must_be_a_positive_integer(monkeypatch, window_id):
    tracking = tracking_module()
    with pytest.raises(ValueError, match='window'):
        tracking.WindowMouseTracker(window_id=window_id)


def test_unknown_mouse_space_is_rejected():
    tracking = tracking_module()
    with pytest.raises(ValueError, match='space'):
        tracking.WindowMouseTracker(mouse_space='frontmost')


def wait_for(predicate):
    deadline = time.monotonic() + 1
    while not predicate():
        assert time.monotonic() < deadline
        time.sleep(.005)


def test_manual_resolution_does_not_block_frames_and_keeps_the_own_window_id(monkeypatch):
    tracking = tracking_module()
    native = quartz_boundary(monkeypatch, {11: window(11), 22: window(22, left=400)}, point=(300, 50))
    entered, release = threading.Event(), threading.Event()
    def resolve(self):
        entered.set()
        release.wait(1)
        return tracking.WindowReference(11)
    monkeypatch.setattr(tracking.WindowMouseTracker, '_resolve_window', resolve)
    tracker = tracking.WindowMouseTracker()
    try:
        assert entered.wait(.3)
        assert tracker.target() == Look(-.25, -.75)
        assert 'screen fallback' in tracker.status()
        release.set()
        wait_for(lambda: tracker.status().startswith('window'))
        assert tracker.target() == Look(1, 0)
        # Reordering/focusing another window must never change the binding.
        native.windows = {22: native.windows[22], 11: native.windows[11]}
        assert tracker.target() == Look(1, 0)
    finally:
        tracker.close()
        release.set()
        tracker._thread.join(timeout=1)


def test_denied_or_missing_terminal_lookup_keeps_screen_fallback_usable(monkeypatch):
    tracking = tracking_module()
    quartz_boundary(monkeypatch, point=(600, 100))
    def denied(self):
        raise RuntimeError('terminal lookup denied')
    monkeypatch.setattr(tracking.WindowMouseTracker, '_resolve_window', denied)
    tracker = tracking.WindowMouseTracker()
    try:
        wait_for(lambda: 'denied' in tracker.status())
        assert tracker.target() == Look(.5, -.5)
        assert 'screen fallback' in tracker.status()
    finally:
        tracker.close()


def test_terminal_lookup_matches_own_tty_once_and_uses_bounded_process(monkeypatch):
    tracking = tracking_module()
    quartz_boundary(monkeypatch, {11: window(11), 22: window(22, left=400)})
    monkeypatch.delenv('TMUX', raising=False)
    monkeypatch.delenv('TMUX_PANE', raising=False)
    monkeypatch.setattr(tracking.os, 'ttyname', lambda _: '/dev/ttys007')
    calls = []
    def run(args, **kwargs):
        calls.append((args, kwargs))
        assert args[0] == '/usr/bin/osascript'
        assert args[-1] == '/dev/ttys007'
        assert 0 < kwargs['timeout'] <= 2
        return SimpleNamespace(returncode=0, stdout='11\t0\t0\t200\t100\n', stderr='')
    monkeypatch.setattr(tracking.subprocess, 'run', run)
    tracker = tracking.WindowMouseTracker()
    try:
        wait_for(lambda: tracker.status().startswith('window'))
        assert tracker.target() == Look(0, 0)
        for _ in range(10):
            tracker.target()
        assert len(calls) == 1
    finally:
        tracker.close()


def test_no_running_supported_terminal_does_not_start_applescript(monkeypatch):
    tracking = tracking_module()
    quartz_boundary(monkeypatch, {11: window(11, owner='Other App')})
    monkeypatch.delenv('TMUX', raising=False)
    monkeypatch.delenv('TMUX_PANE', raising=False)
    monkeypatch.setattr(tracking.os, 'ttyname', lambda _: '/dev/ttys007')
    monkeypatch.setattr(tracking.subprocess, 'run', lambda *a, **k: pytest.fail('must not launch a terminal'))
    tracker = tracking.WindowMouseTracker()
    try:
        wait_for(lambda: 'not running' in tracker.status())
        assert tracker.target() == Look(-.75, -.75)
    finally:
        tracker.close()


def test_tmux_maps_pointer_relative_to_eye_pane_and_refreshes_pane_layout(monkeypatch):
    tracking = tracking_module()
    native = quartz_boundary(monkeypatch, {11: window(11, width=800, height=400)}, point=(400, 200))
    monkeypatch.setenv('TMUX', '/tmp/tmux,1,0')
    monkeypatch.setenv('TMUX_PANE', '%7')
    pane = {'left': 0}
    script_ttys = []
    def run(args, **kwargs):
        assert 0 < kwargs['timeout'] <= 2
        if args[0] == '/usr/bin/osascript':
            script_ttys.append(args[-1])
            return SimpleNamespace(returncode=0, stdout='11\t0\t0\t800\t400', stderr='')
        if 'list-clients' in args:
            return SimpleNamespace(returncode=0, stdout='/dev/ttys002\t80\t40\t@2\t0\n', stderr='')
        return SimpleNamespace(returncode=0,
                               stdout=f'@2\t{pane["left"]}\t0\t40\t40\t80\t40\tbottom\t\t\t80\t40\toff\n', stderr='')
    monkeypatch.setattr(tracking.subprocess, 'run', run)
    tracker = tracking.WindowMouseTracker()
    try:
        wait_for(lambda: tracker.status().startswith('window'))
        assert tracker.target() == Look(1, 0)
        assert 'pane' in tracker.status()
        assert script_ttys == ['/dev/ttys002']
        pane['left'] = 40
        wait_for(lambda: tracker.target() == Look(-1, 0))
        assert script_ttys == ['/dev/ttys002']
    finally:
        tracker.close()


def test_tmux_refresh_cannot_hide_cached_window_bounds_failure(monkeypatch):
    tracking = tracking_module()
    native = quartz_boundary(monkeypatch, point=(600, 100))
    monkeypatch.setenv('TMUX', '/tmp/tmux,1,0')
    monkeypatch.setenv('TMUX_PANE', '%7')
    # Hold the geometry cache clock while the real resolver worker refreshes.
    monkeypatch.setattr(tracking, 'time', SimpleNamespace(monotonic=lambda: native.now))
    refresh_completed, release = threading.Event(), threading.Event()
    context_reads = 0

    def run(args, **kwargs):
        nonlocal context_reads
        assert args[0] == 'tmux', 'an explicit window ID must bypass AppleScript'
        if 'list-clients' in args:
            return SimpleNamespace(returncode=0, stdout='/dev/ttys002\t80\t40\t@2\t0', stderr='')
        if '-c' not in args:
            context_reads += 1
            if context_reads == 3:
                # Reaching the following cycle proves the first layout refresh
                # was published, including any accidental status overwrite.
                refresh_completed.set()
                release.wait(1)
        return SimpleNamespace(returncode=0,
                               stdout='@2\t0\t0\t40\t40\t80\t40\tbottom\t\t\t80\t40\toff', stderr='')

    monkeypatch.setattr(tracking.subprocess, 'run', run)
    tracker = tracking.WindowMouseTracker(window_id=11)
    try:
        wait_for(lambda: tracker.status().startswith('window'))
        assert tracker.target() == Look(.5, -.5)
        assert 'bounds unavailable' in tracker.status()
        assert native.queries == 1
        assert refresh_completed.wait(.9)
        assert tracker.target() == Look(.5, -.5)
        assert native.queries == 1
        assert 'screen fallback' in tracker.status()
        assert 'bounds unavailable' in tracker.status()
        native.windows = {11: window(11, width=800, height=400)}
        native.now = .25
        assert tracker.target() == Look(1, -.5)
        assert tracker.status() == 'window #11 · tmux pane'
        assert native.queries == 2
    finally:
        tracker.close()
        release.set()
        tracker._thread.join(timeout=1)


def test_tmux_top_status_and_client_viewport_offsets_locate_the_visible_pane(monkeypatch):
    tracking = tracking_module()
    quartz_boundary(monkeypatch, {11: window(11, width=800, height=400)}, point=(600, 205))
    monkeypatch.setenv('TMUX', '/tmp/tmux,1,0')
    monkeypatch.setenv('TMUX_PANE', '%7')
    def run(args, **kwargs):
        if args[0] == '/usr/bin/osascript':
            return SimpleNamespace(returncode=0, stdout='11\t0\t0\t800\t400', stderr='')
        if 'list-clients' in args:
            # Control clients do not represent GUI terminal text grids.
            return SimpleNamespace(returncode=0,
                                   stdout='/dev/ttys002\t80\t40\t@2\t0\n/dev/ttys099\t80\t40\t@2\t1', stderr='')
        return SimpleNamespace(returncode=0,
                               stdout='@2\t50\t10\t40\t39\t100\t39\ttop\t10\t10\t80\t40\ton', stderr='')
    monkeypatch.setattr(tracking.subprocess, 'run', run)
    tracker = tracking.WindowMouseTracker()
    try:
        wait_for(lambda: tracker.status().startswith('window'))
        assert tracker.target() == Look(0, 0)
    finally:
        tracker.close()


def test_multiple_tmux_clients_do_not_bind_to_an_arbitrary_terminal_window(monkeypatch):
    tracking = tracking_module()
    quartz_boundary(monkeypatch, {11: window(11, width=800, height=400)}, point=(600, 100))
    monkeypatch.setenv('TMUX', '/tmp/tmux,1,0')
    monkeypatch.setenv('TMUX_PANE', '%7')
    def run(args, **kwargs):
        assert args[0] == 'tmux', 'ambiguous tmux clients must not start terminal lookup'
        if 'list-clients' in args:
            return SimpleNamespace(returncode=0,
                                   stdout='/dev/ttys002\t80\t40\t@2\t0\n/dev/ttys003\t80\t40\t@2\t0', stderr='')
        return SimpleNamespace(returncode=0,
                               stdout='@2\t0\t0\t40\t40\t80\t40\tbottom\t0\t0\t80\t40\toff', stderr='')
    monkeypatch.setattr(tracking.subprocess, 'run', run)
    tracker = tracking.WindowMouseTracker()
    try:
        wait_for(lambda: 'one attached client' in tracker.status())
        assert tracker.target() == Look(.5, -.5)
        assert 'screen fallback' in tracker.status()
    finally:
        tracker.close()


def test_terminal_script_id_absent_from_quartz_is_resolved_by_unique_own_bounds(monkeypatch):
    tracking = tracking_module()
    quartz_boundary(monkeypatch, {11: window(11), 22: window(22, left=400)}, point=(500, 50))
    monkeypatch.delenv('TMUX', raising=False)
    monkeypatch.delenv('TMUX_PANE', raising=False)
    monkeypatch.setattr(tracking.os, 'ttyname', lambda _: '/dev/ttys007')
    monkeypatch.setattr(tracking.subprocess, 'run',
                        lambda *a, **k: SimpleNamespace(returncode=0, stdout='999\t400\t0\t600\t100', stderr=''))
    tracker = tracking.WindowMouseTracker()
    try:
        wait_for(lambda: tracker.status().startswith('window'))
        assert tracker.target() == Look(0, 0)
        assert '#22' in tracker.status()
    finally:
        tracker.close()


def test_manual_lookup_refreshes_quartz_after_terminal_moves_during_applescript(monkeypatch):
    tracking = tracking_module()
    native = quartz_boundary(monkeypatch, {11: window(11)}, point=(500, 50))
    monkeypatch.setattr(tracking.os, 'ttyname', lambda _: '/dev/ttys007')
    def run(*args, **kwargs):
        native.windows = {11: window(11, left=400)}
        return SimpleNamespace(returncode=0, stdout='11\t400\t0\t600\t100', stderr='')
    monkeypatch.setattr(tracking.subprocess, 'run', run)
    tracker = tracking.WindowMouseTracker()
    try:
        wait_for(lambda: tracker.status().startswith('window'))
        assert tracker.target() == Look(0, 0)
    finally:
        tracker.close()


def test_moving_known_native_id_cannot_rebind_to_another_windows_old_rectangle(monkeypatch):
    tracking = tracking_module()
    quartz_boundary(monkeypatch, {11: window(11, left=400), 22: window(22)}, point=(500, 50))
    monkeypatch.setattr(tracking.os, 'ttyname', lambda _: '/dev/ttys007')
    monkeypatch.setattr(tracking.subprocess, 'run',
                        lambda *a, **k: SimpleNamespace(returncode=0, stdout='11\t0\t0\t200\t100', stderr=''))
    tracker = tracking.WindowMouseTracker()
    try:
        wait_for(lambda: 'not found' in tracker.status())
        assert tracker.target() == Look(.25, -.75)
        assert 'screen fallback' in tracker.status()
    finally:
        tracker.close()


def test_iterm_own_tty_is_searched_after_an_unrelated_terminal_denies_lookup(monkeypatch):
    tracking = tracking_module()
    quartz_boundary(monkeypatch, {11: window(11), 22: window(22, left=400, owner='iTerm2')}, point=(500, 50))
    monkeypatch.delenv('TMUX', raising=False)
    monkeypatch.delenv('TMUX_PANE', raising=False)
    monkeypatch.setattr(tracking.os, 'ttyname', lambda _: '/dev/ttys007')
    def run(args, **kwargs):
        if 'com.apple.Terminal' in args[2]:
            return SimpleNamespace(returncode=1, stdout='', stderr='Not authorized')
        return SimpleNamespace(returncode=0, stdout='22\t400\t0\t600\t100', stderr='')
    monkeypatch.setattr(tracking.subprocess, 'run', run)
    tracker = tracking.WindowMouseTracker()
    try:
        wait_for(lambda: tracker.status().startswith('window'))
        assert tracker.target() == Look(0, 0)
        assert '#22' in tracker.status()
    finally:
        tracker.close()
