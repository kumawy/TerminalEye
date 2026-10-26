"""Plan and launch an opt-in wall of native macOS terminal windows.

The temporary Terminal profile runs Python directly, before any interactive
shell can read .zshrc. Each child waits for its own native window ID and then
replaces itself with watch. No shell/tmux panes or startup files are changed.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import plistlib
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import uuid

from .renderer import THEMES


@dataclass(frozen=True)
class Rect:
    """A window rectangle in macOS logical points, with a top-left origin."""

    x: float
    y: float
    width: float
    height: float

    @property
    def right(self):
        return self.x + self.width

    @property
    def bottom(self):
        return self.y + self.height

    @property
    def bounds(self):
        return self.x, self.y, self.right, self.bottom


def quartz_visible_frame(frame, primary_height):
    """Convert an NSScreen visibleFrame, preserving off-primary origins."""
    x, y, width, height = frame
    return Rect(x, primary_height - y - height, width, height)


def read_screens():
    """Read visible screen frames without controlling any applications."""
    if sys.platform != 'darwin':
        raise RuntimeError('wall requires macOS Terminal.app or iTerm2')
    try:
        from AppKit import NSScreen
    except ImportError as error:
        raise RuntimeError('wall needs the macOS mouse extra: pip install -e ".[mouse]"') from error
    screens = list(NSScreen.screens())
    if not screens:
        raise RuntimeError('No macOS screens are available')
    primary = screens[0].frame()
    primary_top = primary.origin.y + primary.size.height
    return [quartz_visible_frame((screen.visibleFrame().origin.x, screen.visibleFrame().origin.y,
                                  screen.visibleFrame().size.width, screen.visibleFrame().size.height),
                                 primary_top) for screen in screens]


def _in_range(value, low, high, label):
    if (isinstance(value, bool) or not isinstance(value, int) or value < low or
            (high is not None and value > high)):
        interval = f'from {low} to {high}' if high is not None else f'at least {low}'
        raise ValueError(f'{label} must be an integer {interval}')


def layout_windows(screen, *, count=9, columns=None, gap=12, margin=12):
    """Fit whole-point cells inside the usable screen, rejecting small grids."""
    _in_range(count, 1, 36, 'count')
    _in_range(gap, 0, 80, 'gap')
    _in_range(margin, 0, 120, 'margin')
    if columns is not None:
        _in_range(columns, 1, min(12, count), 'columns')
    columns = columns or math.ceil(math.sqrt(count))
    rows = math.ceil(count / columns)
    if not all(math.isfinite(value) for value in (screen.x, screen.y, screen.width, screen.height)):
        raise ValueError('screen frame must contain finite points')
    left = math.ceil(screen.x) + margin
    top = math.ceil(screen.y) + margin
    width = math.floor(screen.right) - margin - left
    height = math.floor(screen.bottom) - margin - top
    cell_width = (width - gap * (columns - 1)) // columns
    cell_height = (height - gap * (rows - 1)) // rows
    # 21x10 renderer cells plus native window chrome, using a compact 10pt font.
    if cell_width < 220 or cell_height < 160:
        raise ValueError('Windows would be too small to fit an eye (minimum 220x160 points). '
                         'Use fewer windows/columns, smaller gap/margin, or a larger screen.')
    return tuple(Rect(left + (index % columns) * (cell_width + gap),
                      top + (index // columns) * (cell_height + gap), cell_width, cell_height)
                 for index in range(count))


def _integer(low, high=None):
    def parse(value):
        try:
            number = int(value)
            _in_range(number, low, high, 'value')
            return number
        except ValueError as error:
            raise argparse.ArgumentTypeError(str(error)) from error
    return parse


def add_parser(subparsers):
    parser = subparsers.add_parser('wall', help='open a grid of native eye windows on macOS')
    parser.add_argument('--count', type=_integer(1, 36), default=9, help='windows, 1..36 (default: 9)')
    parser.add_argument('--columns', type=_integer(1, 12), help='columns (default: square grid)')
    parser.add_argument('--gap', type=_integer(0, 80), default=12, help='gap in points (default: 12)')
    parser.add_argument('--margin', type=_integer(0, 120), default=12, help='screen inset in points (default: 12)')
    parser.add_argument('--screen', type=_integer(1), default=1, help='screen number, starting at 1')
    parser.add_argument('--app', choices=('terminal', 'iterm'), default='terminal')
    parser.add_argument('--theme', choices=(*THEMES, 'mixed'), default='matrix')
    parser.add_argument('--mode', choices=('mouse', 'idle'), default='mouse')
    parser.add_argument('--fps', type=_integer(2, 30), default=12)
    parser.add_argument('--style', choices=('ascii', 'blocks'), default='ascii')
    parser.add_argument('--dry-run', action='store_true', help='print layout and commands; open no windows')
    return parser


def watch_argv(args, theme):
    # Do not resolve the interpreter symlink: that would discard the user's venv.
    return [os.path.abspath(sys.executable), '-m', 'eyeterm', 'watch', '--mode', args.mode,
            '--theme', theme, '--fps', str(args.fps), '--style', args.style,
            '--mouse-space', 'window', '--minimal']


def bootstrap_command(spec, *, interpreter=None, project=None):
    """A directly executed env/Python command; quote every data-bearing word."""
    interpreter = os.path.abspath(interpreter or sys.executable)
    project = str(project or Path(__file__).resolve().parent.parent)
    return shlex.join(['/usr/bin/env', 'TERMINAL_EYE_DISABLE=1', 'TERMINAL_EYE_ACTIVE=1',
                       f'PYTHONPATH={project}', interpreter, '-m', 'eyeterm.wall', '--child', str(spec)])


def _archive(class_name, value):
    """Encode the small NSFont/NSColor objects used in exported .terminal files."""
    value = dict(value, **{'$class': plistlib.UID(2)})
    return plistlib.dumps({'$archiver': 'NSKeyedArchiver', '$version': 100000,
                          '$top': {'root': plistlib.UID(1)}, '$objects': [
                              '$null', value, {'$classname': class_name,
                                               '$classes': [class_name, 'NSObject']}, 'Menlo-Regular']},
                         fmt=plistlib.FMT_BINARY)


def terminal_profile(command, title):
    return {'name': 'Terminal Eye Wall', 'type': 'Window Settings',
            'CommandString': command, 'RunCommandAsShell': False,
            'CustomTitle': title, 'ShowWindowSettingsNameInTitle': False,
            'ShowWindowTitle': True, 'ShowShellCommandInTitle': False,
            'ShowActiveProcessInTitle': False, 'ShowDimensionsInTitle': False,
            'shellExitAction': 0, 'ProfileCurrentVersion': 2.09,
            'Font': _archive('NSFont', {'NSName': plistlib.UID(3), 'NSSize': 10.0, 'NSfFlags': 16}),
            'BackgroundColor': _archive('NSColor', {'NSColorSpace': 1, 'NSRGB': b'0.008 0.012 0.009 1\0'}),
            'TextColor': _archive('NSColor', {'NSColorSpace': 1, 'NSRGB': b'0.2 0.95 0.4 1\0'}),
            'CursorColor': _archive('NSColor', {'NSColorSpace': 1, 'NSRGB': b'0.2 0.95 0.4 1\0'})}


_FIT_WINDOW = '''
function fitWindow(win, values) {
    var left = Number(values[0]), top = Number(values[1]);
    var right = Number(values[2]), bottom = Number(values[3]);
    var width = right - left, height = bottom - top;
    for (var attempt = 0; attempt < 8; attempt++) {
        win.bounds = {x: left, y: top, width: width, height: height};
        var actual = win.bounds();
        if (actual.x >= left && actual.y >= top && actual.x + actual.width <= right &&
                actual.y + actual.height <= bottom) {
            return JSON.stringify({id: Number(win.id()), bounds: actual});
        }
        if (actual.x < left || actual.x + actual.width > right) {
            width -= Math.max(8, Math.ceil(actual.x + actual.width - right));
        }
        if (actual.y < top || actual.y + actual.height > bottom) {
            height -= Math.max(14, Math.ceil(actual.y + actual.height - bottom));
        }
        if (width < 160 || height < 120) break;
    }
    throw new Error("The terminal profile's minimum window size does not fit this grid. Use fewer windows.");
}
'''

_TERMINAL_SCRIPT = _FIT_WINDOW + '''
function run(argv) {
    var app = Application('com.apple.Terminal');
    if (!app.running()) app.launch();
    var before = app.windows().map(function(win) { return win.id(); });
    app.open(Path(argv[0]));
    for (var attempt = 0; attempt < 100; attempt++) {
        var windows = app.windows();
        for (var index = 0; index < windows.length; index++) {
            var win = windows[index];
            if (before.indexOf(win.id()) !== -1) continue;
            var tabs = win.tabs();
            for (var tab = 0; tab < tabs.length; tab++) {
                if (tabs[tab].customTitle() === argv[1]) {
                    return fitWindow(win, argv.slice(2, 6));
                }
            }
        }
        delay(0.05);
    }
    throw new Error('Terminal did not open the fresh eye profile. No existing windows were changed.');
}
'''

_ITERM_SCRIPT = _FIT_WINDOW + '''
function run(argv) {
    var app = Application('com.googlecode.iterm2');
    if (!app.running()) app.launch();
    var win = app.createWindowWithDefaultProfile({command: argv[0]});
    win.currentSession().name = argv[1];
    return fitWindow(win, argv.slice(2, 6));
}
'''


def render_script(app):
    """Raw JXA Apple Events source, suitable for osacompile -l JavaScript."""
    if app not in ('terminal', 'iterm'):
        raise ValueError('app must be terminal or iterm')
    return _TERMINAL_SCRIPT if app == 'terminal' else _ITERM_SCRIPT


def _validate_args(args):
    _in_range(args.count, 1, 36, 'count')
    _in_range(args.gap, 0, 80, 'gap')
    _in_range(args.margin, 0, 120, 'margin')
    _in_range(args.screen, 1, None, 'screen')
    _in_range(args.fps, 2, 30, 'fps')
    if args.columns is not None:
        _in_range(args.columns, 1, min(12, args.count), 'columns')
    for name, allowed in (('mode', ('mouse', 'idle')), ('app', ('terminal', 'iterm')),
                          ('theme', (*THEMES, 'mixed')), ('style', ('ascii', 'blocks'))):
        if getattr(args, name) not in allowed:
            raise ValueError(f'{name} must be one of {", ".join(allowed)}')


def _window_identity(result, app):
    if result.returncode:
        detail = (result.stderr or result.stdout or 'unknown Apple Events error').strip()
        if '-1743' in detail or 'not authorized' in detail.lower():
            raise RuntimeError(f'Automation access to {app} was denied. Allow the launching application '
                               'in System Settings > Privacy & Security > Automation, then retry.')
        raise RuntimeError(f'Could not create the {app} eye window: {detail}')
    try:
        payload = json.loads(result.stdout)
        identifier = payload['id']
        bounds = payload['bounds']
        rect = Rect(*(float(bounds[key]) for key in ('x', 'y', 'width', 'height')))
        if (isinstance(identifier, bool) or not isinstance(identifier, int) or identifier < 1 or
                rect.width <= 0 or rect.height <= 0 or
                not all(math.isfinite(value) for value in (rect.x, rect.y, rect.width, rect.height))):
            raise ValueError('invalid window identity')
        return identifier, rect
    except (TypeError, KeyError, ValueError) as error:
        raise RuntimeError(f'{app} did not return a valid native window identity') from error


def read_native_windows():
    """Read only owner, ID and geometry; no screen recording or app control."""
    try:
        import Quartz
    except ImportError as error:
        raise RuntimeError('wall needs the macOS mouse extra: pip install -e ".[mouse]"') from error
    flags = Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements
    return Quartz.CGWindowListCopyWindowInfo(flags, Quartz.kCGNullWindowID) or []


def canonical_window_id(identifier, rect, app, records):
    """Require a verified CGWindowID; never fall back to screen-wide tracking."""
    owners = {'Terminal'} if app == 'terminal' else {'iTerm2', 'iTerm'}
    matches = []
    for item in records:
        if item.get('kCGWindowOwnerName') not in owners or item.get('kCGWindowLayer', 0) != 0:
            continue
        try:
            bounds = item['kCGWindowBounds']
            native_rect = tuple(float(bounds[key]) for key in ('X', 'Y', 'Width', 'Height'))
            native_id = int(item['kCGWindowNumber'])
        except (KeyError, ValueError, TypeError):
            continue
        if native_id < 1 or not all(abs(a - b) <= 1 for a, b in zip(
                native_rect, (rect.x, rect.y, rect.width, rect.height))):
            continue
        if native_id == identifier:
            return str(native_id)
        matches.append(native_id)
    if len(matches) == 1:
        return str(matches[0])
    reason = 'ambiguous matching bounds' if len(matches) > 1 else 'no matching owned bounds'
    raise RuntimeError(f'Could not identify the new {app} native window: {reason}. '
                       'The eye was not started with screen-wide tracking.')


def await_native_id(identifier, rect, app, native_reader):
    """Allow a short WindowServer update delay after an Apple Events resize."""
    for attempt in range(20):
        try:
            return canonical_window_id(identifier, rect, app, native_reader())
        except RuntimeError:
            if attempt == 19:
                raise
            time.sleep(.05)


def run_wall(args, *, screens=None, runner=None, native_reader=None, out=None):
    """Validate and plan everything before the first native window is opened."""
    _validate_args(args)
    screens = read_screens() if screens is None else screens
    if args.screen > len(screens):
        raise ValueError(f'screen {args.screen} is unavailable; found {len(screens)} screen(s)')
    cells = layout_windows(screens[args.screen - 1], count=args.count, columns=args.columns,
                           gap=args.gap, margin=args.margin)
    themes = [THEMES[index % len(THEMES)] if args.theme == 'mixed' else args.theme
              for index in range(args.count)]
    output = out or sys.stdout
    if args.dry_run:
        columns = args.columns or math.ceil(math.sqrt(args.count))
        plan = {'app': args.app, 'screen': args.screen, 'columns': columns,
                'rows': math.ceil(args.count / columns), 'gap': args.gap, 'margin': args.margin,
                'windows': [{'index': index + 1, 'bounds': cell.bounds, 'theme': theme,
                             'command': shlex.join(watch_argv(args, theme) + ['--window-id', '<new-window-id>'])}
                            for index, (cell, theme) in enumerate(zip(cells, themes))]}
        print(json.dumps(plan, ensure_ascii=False, indent=2), file=output)
        return 0
    runner = runner or subprocess.run
    native_reader = native_reader or read_native_windows
    directory = Path(tempfile.mkdtemp(prefix='terminal-eye-wall-'))
    sentinel = directory / '.launching'
    sentinel.touch()
    launched = []
    try:
        for index, (cell, theme) in enumerate(zip(cells, themes)):
            spec = directory / f'{index}.json'
            spec.write_text(json.dumps({'argv': watch_argv(args, theme)}), encoding='utf-8')
            title = f'Terminal Eye {index + 1} · {uuid.uuid4().hex}'
            command = bootstrap_command(spec)
            if args.app == 'terminal':
                profile = spec.with_suffix('.terminal')
                profile.write_bytes(plistlib.dumps(terminal_profile(command, title)))
                data = [str(profile), title]
            else:
                data = [command, title]
            try:
                result = runner(['/usr/bin/osascript', '-l', 'JavaScript', '-', *data,
                                 *(str(value) for value in cell.bounds)],
                                input=render_script(args.app), text=True, capture_output=True,
                                check=False, timeout=15)
            except (OSError, subprocess.TimeoutExpired) as error:
                raise RuntimeError(f'Could not create the {args.app} eye window: {error}') from error
            identifier, actual = _window_identity(result, args.app)
            window_id = await_native_id(identifier, actual, args.app, native_reader)
            spec.with_suffix('.id.tmp').write_text(window_id, encoding='ascii')
            spec.with_suffix('.id.tmp').replace(spec.with_suffix('.id'))
            launched.append(window_id)
    except BaseException:
        # A launched child might still be loading. It owns its files from here;
        # never close windows or remove another child's pending startup data.
        if not launched:
            shutil.rmtree(directory, ignore_errors=True)
        raise
    finally:
        sentinel.unlink(missing_ok=True)
        try:
            directory.rmdir()
        except OSError:
            pass  # A pending child removes the directory after consuming its files.
    print(f'Opened {len(launched)} eye windows in {args.app}: {", ".join(launched)}', file=output)
    return 0


def run_child(spec):
    """Receive an owned window ID, then exec watch without an interactive shell."""
    spec = Path(spec)
    identity = spec.with_suffix('.id')
    try:
        payload = json.loads(spec.read_text(encoding='utf-8'))
        deadline = time.monotonic() + 20
        while not identity.exists():
            if time.monotonic() >= deadline:
                raise RuntimeError('The wall launcher did not provide this eye window ID. Close this window and retry.')
            time.sleep(.05)
        window_id = identity.read_text(encoding='ascii').strip()
        if not window_id.isdecimal() or int(window_id) < 1:
            raise RuntimeError('The wall launcher returned an invalid window ID')
        argv = payload['argv'] + ['--window-id', window_id]
        environment = dict(os.environ, TERMINAL_EYE_DISABLE='1', TERMINAL_EYE_ACTIVE='1')
    finally:
        for owned in (spec, identity, spec.with_suffix('.terminal')):
            owned.unlink(missing_ok=True)
        try:
            spec.parent.rmdir()
        except OSError:
            pass  # The parent sentinel or other children may still be present.
    os.execve(argv[0], argv, environment)
    return 0


if __name__ == '__main__':
    if len(sys.argv) != 3 or sys.argv[1] != '--child':
        raise SystemExit('Use terminal-eye wall to create native eye windows.')
    try:
        raise SystemExit(run_child(Path(sys.argv[2])))
    except (OSError, RuntimeError, ValueError) as error:
        raise SystemExit(f'Terminal Eye: {error}')
