"""Read the global pointer relative to this terminal's stable native window.

Window metadata needs neither an event tap nor image capture. AppleScript is a
bounded, one-time tty lookup on a worker; it never runs on the rendering path.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import os
import subprocess
import threading
import time

from .trackers import Look, _bounded, mouse_target


@dataclass(frozen=True)
class WindowReference:
    window_id: int
    pane: tuple[float, float, float, float] | None = None


def _quartz():
    try:
        import Quartz
    except ImportError as exc:
        raise RuntimeError('Mouse support missing. Install: pip install -e ".[mouse]"') from exc
    return Quartz


def _rect(record):
    bounds = record.get('kCGWindowBounds', {})
    values = tuple(float(bounds[key]) for key in ('X', 'Y', 'Width', 'Height'))
    if not all(math.isfinite(value) for value in values) or min(values[2:]) <= 0:
        raise ValueError('unusable window bounds')
    return values


def _run(args):
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=1.5,
                                check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError('terminal lookup unavailable or timed out') from exc
    if result.returncode:
        raise RuntimeError('terminal lookup unavailable or permission denied')
    return result.stdout.strip()


def _lookup_script(bundle):
    """Read only tty/id/bounds; the guard must precede any app event."""
    if bundle == 'com.apple.Terminal':
        scan = '''repeat with t in tabs of w
                    if (tty of t) is wantedTTY then
                        return my describeWindow(w)
                    end if
                end repeat'''
    else:
        scan = '''repeat with t in tabs of w
                    repeat with s in sessions of t
                        if (tty of s) is wantedTTY then
                            return my describeWindow(w)
                        end if
                    end repeat
                end repeat'''
    return f'''on run argv
    set wantedTTY to item 1 of argv
    if not (application id "{bundle}" is running) then return ""
    with timeout of 1 seconds
        tell application id "{bundle}"
            repeat with w in windows
                {scan}
            end repeat
        end tell
    end timeout
    return ""
end run
on describeWindow(w)
    tell application id "{bundle}"
        set b to bounds of w
        return ((id of w) as text) & tab & (item 1 of b as text) & tab & (item 2 of b as text) & tab & (item 3 of b as text) & tab & (item 4 of b as text)
    end tell
end describeWindow'''


class WindowMouseTracker:
    """Keep a window binding stable across focus changes and mode switches.

    An explicit CGWindowID avoids AppleScript entirely. Manual lookup and tmux
    layout refresh run in one daemon worker. Quartz window bounds are cached for
    a quarter second; the global pointer itself is read on every target call.
    """

    def __init__(self, mouse_space='window', window_id=None):
        if mouse_space not in ('window', 'screen'):
            raise ValueError('mouse space must be window or screen')
        if window_id is not None and (type(window_id) is not int or window_id <= 0):
            raise ValueError('window id must be a positive integer')
        self._space = mouse_space
        self._reference = WindowReference(window_id) if window_id is not None else None
        self._resolver_reason = ''
        self._bounds_reason = ''
        self._bounds = None
        self._next_bounds = 0.
        self._owner = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._tmux_pane = os.environ.get('TMUX_PANE') if os.environ.get('TMUX') else None
        self._client_tty = None
        self._thread = None
        if mouse_space == 'window' and (window_id is None or self._tmux_pane):
            self._resolver_reason = 'resolving terminal window'
            self._thread = threading.Thread(target=self._resolve_loop,
                                            name='terminal-eye-window', daemon=True)
            self._thread.start()

    def _resolve_loop(self):
        try:
            reference = self._resolve_window()
            if not self._stop.is_set():
                with self._lock:
                    self._reference = reference
                    self._resolver_reason = ''
            while self._tmux_pane and not self._stop.wait(.25):
                try:
                    _, pane = self._tmux_context(self._client_tty)
                    if self._stop.is_set():
                        break
                    with self._lock:
                        self._reference = WindowReference(reference.window_id, pane)
                        self._resolver_reason = ''
                except Exception as exc:
                    if self._stop.is_set():
                        break
                    with self._lock:
                        self._resolver_reason = str(exc)
        except Exception as exc:
            with self._lock:
                self._resolver_reason = str(exc)

    def _resolve_window(self):
        pane = None
        if self._tmux_pane:
            tty, pane = self._tmux_context()
            self._client_tty = tty
        else:
            tty = None
            for descriptor in (1, 0, 2):
                try:
                    tty = os.ttyname(descriptor)
                    break
                except OSError:
                    pass
            if tty is None:
                raise RuntimeError('own terminal tty unavailable')
        if self._reference is not None:
            return WindowReference(self._reference.window_id, pane)
        quartz = _quartz()
        records = quartz.CGWindowListCopyWindowInfo(quartz.kCGWindowListOptionAll,
                                                   quartz.kCGNullWindowID) or []
        apps = [('com.apple.Terminal', {'Terminal'}),
                ('com.googlecode.iterm2', {'iTerm', 'iTerm2'})]
        candidates = [(bundle, names) for bundle, names in apps
                      if any(record.get('kCGWindowOwnerName') in names for record in records)]
        if not candidates:
            raise RuntimeError('supported terminal not running')
        errors = []
        for bundle, names in candidates:
            for _ in range(2):
                if self._stop.is_set():
                    raise RuntimeError('terminal lookup closed')
                try:
                    answer = _run(['/usr/bin/osascript', '-e', _lookup_script(bundle), '--', tty])
                except RuntimeError as exc:
                    errors.append(str(exc))
                    break
                if not answer:
                    break
                try:
                    fields = answer.split('\t')
                    native_id = int(fields[0])
                    if native_id <= 0:
                        raise ValueError('invalid window id')
                    left, top, right, bottom = map(float, fields[1:])
                    expected = (left, top, right - left, bottom - top)
                    # The app may move while AppleScript is answering. Never
                    # match that answer against the earlier running-app snapshot.
                    records = quartz.CGWindowListCopyWindowInfo(
                        quartz.kCGWindowListOptionAll, quartz.kCGNullWindowID) or []
                    own_records = [record for record in records
                                   if record.get('kCGWindowOwnerName') in names]
                    exact = [record for record in own_records
                             if record.get('kCGWindowNumber') == native_id]
                    if len(exact) == 1:
                        if _rect(exact[0]) == expected:
                            return WindowReference(native_id, pane)
                        # Retry a geometry race using the tty. An old rectangle
                        # must not redirect a known native id to another window.
                        continue
                    # Terminal's scripting id is not documented as a CGWindowID.
                    # Accept a unique rectangle only if that native id is absent.
                    matches = [record for record in own_records if _rect(record) == expected]
                    if not exact and len(matches) == 1:
                        return WindowReference(int(matches[0]['kCGWindowNumber']), pane)
                except (KeyError, ValueError, TypeError):
                    break
        raise RuntimeError(errors[-1] if errors else 'own terminal window not found')

    def _tmux_context(self, pinned_tty=None):
        format_string = ('#{window_id}\t#{pane_left}\t#{pane_top}\t#{pane_width}'
                         '\t#{pane_height}\t#{window_width}\t#{window_height}'
                         '\t#{status-position}\t#{window_offset_x}\t#{window_offset_y}'
                         '\t#{client_width}\t#{client_height}\t#{status}')
        command = ['tmux', 'display-message', '-p', '-t', self._tmux_pane]
        fields = _run(command + [format_string]).split('\t')
        if len(fields) != 13:
            raise RuntimeError('tmux pane geometry unavailable')
        clients = _run(['tmux', 'list-clients', '-F',
                        '#{client_tty}\t#{client_width}\t#{client_height}\t#{window_id}'
                        '\t#{client_control_mode}'])
        clients = [line.split('\t') for line in clients.splitlines()]
        clients = [client for client in clients if len(client) == 5 and client[3] == fields[0]
                   and client[4] == '0'
                   and (pinned_tty is None or client[0] == pinned_tty)]
        if len(clients) != 1:
            raise RuntimeError('tmux needs one attached client viewing this pane')
        tty = clients[0][0]
        # Bind geometry to the same client, rather than whichever client tmux
        # considers most recent when it evaluates a pane-only target.
        fields = _run(command + ['-c', tty, format_string]).split('\t')
        try:
            left, top, width, height = map(int, fields[1:5])
            # tmux omits offsets when the entire window fits this client.
            offset_x, offset_y = (int(value or '0') for value in fields[8:10])
            columns, rows = map(int, fields[10:12])
            status_rows = {'on': 1, 'off': 0}.get(fields[12])
            if status_rows is None:
                status_rows = int(fields[12])
            left, top = left - offset_x, top - offset_y
            if fields[7] == 'top':
                top += status_rows
            if (min(width, height, columns, rows) <= 0 or min(left, top) < 0
                    or left + width > columns or top + height > rows):
                raise ValueError('pane outside client viewport')
        except (ValueError, IndexError):
            raise RuntimeError('tmux pane geometry unavailable') from None
        return tty, (left / columns, top / rows, width / columns, height / rows)

    def target(self) -> Look:
        if self._space == 'screen':
            return mouse_target()
        with self._lock:
            reference, reason = self._reference, self._resolver_reason
        if reference is None or (self._tmux_pane and reason):
            return mouse_target()
        now = time.monotonic()
        if now >= self._next_bounds:
            self._next_bounds = now + .25
            try:
                quartz = _quartz()
                records = quartz.CGWindowListCopyWindowInfo(
                    quartz.kCGWindowListOptionIncludingWindow, reference.window_id) or []
                records = [record for record in records
                           if record.get('kCGWindowNumber') == reference.window_id]
                if len(records) != 1:
                    raise ValueError('own window unavailable')
                record = records[0]
                owner = (record.get('kCGWindowOwnerPID'), record.get('kCGWindowOwnerName'))
                if self._owner is not None and self._owner != owner:
                    raise ValueError('own window owner changed')
                self._bounds = _rect(record)
                self._owner = owner
                with self._lock:
                    self._bounds_reason = ''
            except Exception:
                self._bounds = None
                with self._lock:
                    self._bounds_reason = 'own window bounds unavailable'
        if self._bounds is None:
            return mouse_target()
        quartz = _quartz()
        try:
            event = quartz.CGEventCreate(None)
            if event is None:
                raise RuntimeError('Mouse cursor could not be read from Quartz.')
            point = quartz.CGEventGetLocation(event)
        except Exception as exc:
            raise RuntimeError('Mouse cursor could not be read through Quartz.') from exc
        left, top, width, height = self._bounds
        if reference.pane is not None:
            pane_left, pane_top, pane_width, pane_height = reference.pane
            left, top = left + pane_left * width, top + pane_top * height
            width, height = width * pane_width, height * pane_height
        return _bounded(2 * (point.x - left) / width - 1,
                        2 * (point.y - top) / height - 1)

    def status(self):
        if self._space == 'screen':
            return 'screen'
        with self._lock:
            reference = self._reference
            reason = self._bounds_reason or self._resolver_reason
        if reason or reference is None:
            return f'screen fallback ({reason or "own window unavailable"})'
        return f'window #{reference.window_id}' + (' · tmux pane' if reference.pane else '')

    def close(self):
        self._stop.set()
