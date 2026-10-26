"""Foreground CLI for splash, a persistent eye pane, and opt-in zsh startup."""
from __future__ import annotations

import argparse
import math
import shutil
import sys
import time

from .animation import Blink, GazeSmoother, Idle, ease
from .hooks import HOOK_END, HOOK_START, install_hook
from .diagnostics import NativeDiagnostics
from .controls import Controls, build_controls
from .modes import ModeController
from .renderer import THEMES, THEME_DESCRIPTIONS, render_eye, to_ansi
from .terminal import TerminalExit, TerminalSession
from .trackers import CameraTracker, Look, mouse_target
from .wall import add_parser as add_wall_parser, run_wall


def dims(args, terminal_size=None):
    columns, rows = terminal_size or shutil.get_terminal_size((100, 32))
    return (min(args.width or min(180, columns - 1), max(0, columns - 1), 220),
            min(args.height or min(60, rows - 2), max(0, rows - 2), 65))


def frame(args, look: Look, openness: float = 1.0, *, terminal_size=None):
    width, height = dims(args, terminal_size)
    if width < 20 or height < 8:
        return 'Terminal too small: need 21x10 cells'[:width]
    picture = render_eye(width, height, look.x, look.y, openness,
                         style=args.style, cell_aspect=args.cell_aspect,
                         theme=args.theme)
    return to_ansi(picture, style=args.style, colors=not args.mono)


def animate_intro(args):
    if not sys.stdout.isatty():
        return
    # The normal splash preserves the shell's contents and scrollback. --keep
    # explicitly chooses a foreground preview in the ordinary screen instead.
    duration = args.duration
    start = time.monotonic()
    with TerminalSession(alternate=not args.keep) as screen:
        while True:
            now = time.monotonic()
            progress = min(1.0, (now - start) / duration)
            openness = ease(progress / .75)
            look = Look(.15 * math.sin(progress * 7), -.06 * math.sin(progress * 9))
            screen.draw(frame(args, look, openness, terminal_size=screen.size()))
            if progress >= 1:
                break
            time.sleep(min(1 / 16, max(0, duration - (time.monotonic() - start))))
        if args.keep:
            _, height = dims(args, screen.size())
            sys.stdout.write(f'\x1b[{height + 1};1H\x1b[K')
            sys.stdout.flush()


def camera_status(camera, debug: bool=False) -> str:
    """Expose successful detection separately from a centered/no-face gaze."""
    snapshot = camera.status()
    labels = {'starting': 'STARTING', 'tracking': 'FACE FOUND',
              'no-face': 'NO FACE', 'stalled': 'NO FRAMES',
              'closed': 'CLOSED', 'error': 'ERROR'}
    text = f"camera: {labels.get(snapshot.state, 'STARTING')}"
    if debug:
        text += f' x={snapshot.look.x:+.2f} y={snapshot.look.y:+.2f} · {snapshot.backend}'
    return text + ' · Ctrl+C'


def watch_view(args, look: Look, openness: float, size: tuple[int,int],
               mode: str, status: str):
    """Render above a cell-aligned status/control bar, reserving the last row."""
    columns, rows = size
    if getattr(args, 'minimal', False):
        warning = 'ERROR' in status or 'fallback' in status.lower()
        picture = frame(args,look,openness,terminal_size=(columns,rows-int(warning)))
        if warning:
            picture = picture.rstrip('\n') + '\n' + status
        return picture,Controls((),()),0
    bar = Controls((),()) if args.no_controls else build_controls(max(0,columns-1),mode,colors=not args.mono)
    picture = frame(args,look,openness,terminal_size=(columns,rows-len(bar.lines)))
    lines = picture.splitlines()
    lines.append(status)
    top_row = len(lines)+1
    lines.extend(bar.lines)
    return '\n'.join(lines),bar,top_row


def animate_watch(args):
    if not sys.stdout.isatty():
        raise RuntimeError('watch requires an interactive TTY terminal')
    if args.mode == 'camera':
        print('Camera mode: opening the selected webcam for local face-position tracking. '
              'No recording or uploads. Ctrl+C stops and releases the camera.',
              file=sys.stderr, flush=True)
    # Clickable watch may explicitly switch into Face mode later. Capture native
    # diagnostics throughout the owned screen, including camera close on exit.
    with NativeDiagnostics(enabled=args.mode == 'camera' or not args.no_controls):
        controller = None
        try:
            with TerminalSession(interactive=not args.no_controls) as screen:
                controller = ModeController(args.mode,camera_index=args.camera_index,
                                            invert_x=args.invert_x,camera_backend=args.camera_backend,
                                            mouse_space=args.mouse_space,window_id=args.window_id)
                blink = Blink()
                smoother = GazeSmoother(args.smoothing)
                start = previous = time.monotonic()
                interval = 1 / args.fps
                last_bar = Controls((),())
                last_top = 0
                last_size = None
                last_look = Look(0,0)
                quit_requested = False
                shortcuts = {'m':'mouse','1':'mouse','f':'camera','c':'camera','2':'camera',
                             'a':'idle','i':'idle','3':'idle'}
                while True:
                    now = time.monotonic()
                    if args.seconds is not None and now - start >= args.seconds:
                        break
                    size = screen.size()
                    pending = None
                    if not args.no_controls:
                        for event in screen.read_events():
                            if event.kind == 'key':
                                if event.key == 'q':
                                    quit_requested = True
                                elif event.key == 't':
                                    args.theme = THEMES[(THEMES.index(args.theme)+1) % len(THEMES)]
                                elif event.key in shortcuts:
                                    pending = shortcuts[event.key]
                            elif (event.kind == 'click' and event.button == 0 and
                                  size == last_size and event.x is not None and event.y is not None
                                  and 1 <= event.x < size[0] and 1 <= event.y <= size[1]):
                                chosen = last_bar.hit_test(event.x,event.y,top_row=last_top)
                                if chosen is not None:
                                    pending = chosen
                    if quit_requested:
                        break
                    # At most one transition per poll: a paste/event flood must
                    # never repeatedly open and close native camera handles.
                    if pending is not None:
                        if pending == 'camera' and controller.mode != 'camera':
                            opening,_,_ = watch_view(args,last_look,1,size,controller.mode,
                                                    'Face mode: opening camera...')
                            screen.draw(opening)
                        controller.switch(pending)
                    desired = controller.target(now)
                    if controller.mode != 'idle':
                        gain = args.tracking_gain
                        if gain is None:
                            gain = 2.5 if controller.mode == 'camera' else 1.0
                        desired = Look(desired.x * gain,desired.y * gain)
                    look = smoother.sample(desired,now-previous)
                    previous = now
                    last_look = look
                    if args.no_controls and controller.mode == 'idle' and not controller.message:
                        status = f'Terminal Eye · {controller.mode} · Ctrl+C to exit'
                    else:
                        status = (controller.message or controller.status(args.debug_tracking))
                        if not args.no_controls:
                            status += f' · {args.theme} · t color'
                        status += ' · Ctrl+C to exit' if args.no_controls else ' · q quit'
                    picture,last_bar,last_top = watch_view(args,look,blink.sample(now),size,
                                                          controller.mode,status)
                    screen.draw(picture)
                    last_size = size
                    delay = max(0,interval-(time.monotonic()-now))
                    if args.seconds is not None:
                        delay = min(delay,max(0,args.seconds-(time.monotonic()-start)))
                    time.sleep(delay)
        finally:
            if controller is not None:
                controller.close()


def _integer_range(low, high=None):
    def parse(value):
        try:
            number = int(value)
        except ValueError as exc:
            raise argparse.ArgumentTypeError('must be an integer') from exc
        if number < low or (high is not None and number > high):
            upper = f'..{high}' if high is not None else ' or greater'
            raise argparse.ArgumentTypeError(f'must be {low}{upper}')
        return number
    return parse


def _float_range(low, high=None, *, exclusive_low=False):
    def parse(value):
        try:
            number = float(value)
        except ValueError as exc:
            raise argparse.ArgumentTypeError('must be a finite number') from exc
        if (not math.isfinite(number) or number < low or
                (exclusive_low and number == low) or (high is not None and number > high)):
            relation = 'greater than' if exclusive_low else 'at least'
            upper = f' and at most {high}' if high is not None else ''
            raise argparse.ArgumentTypeError(f'must be finite, {relation} {low}{upper}')
        return number
    return parse


def main(argv=None):
    parser = argparse.ArgumentParser(prog='terminal-eye', description='Animated terminal eye for macOS')
    sub = parser.add_subparsers(dest='cmd', required=True)

    def options(command):
        command.add_argument('--width', type=_integer_range(20, 220),
                             help='Character columns, default pane width, up to 180 (clamped)')
        command.add_argument('--height', type=_integer_range(8, 65),
                             help='Character rows, default pane height, up to 60 (clamped)')
        command.add_argument('--style', choices=('ascii', 'blocks'), default='ascii',
                             help='Dense truecolor ASCII, or Unicode half-blocks')
        command.add_argument('--mono', action='store_true', help='Disable ANSI truecolor')
        command.add_argument('--cell-aspect', type=_float_range(.25, 1), default=.5,
                             help='Cell width / cell height, default 0.5')
        command.add_argument('--theme', choices=THEMES, default='original')

    intro = sub.add_parser('intro', help='Play awakening splash, then return control to shell')
    options(intro)
    intro.add_argument('--duration', type=_float_range(0, 30, exclusive_low=True), default=1.3)
    intro.add_argument('--keep', action='store_true', help='Leave the preview in the ordinary screen')
    watch = sub.add_parser('watch', help='Continuous eye; use a separate window or tmux pane')
    options(watch)
    watch.add_argument('--mode', choices=('idle', 'mouse', 'camera'), default='idle')
    watch.add_argument('--fps', type=_integer_range(2, 30), default=16)
    watch.add_argument('--smoothing', type=_float_range(0, 10), default=.12,
                       help='Gaze smoothing time constant in seconds; 0 = immediate')
    watch.add_argument('--tracking-gain', type=_float_range(.1, 8), default=None,
                       help='Movement sensitivity, default face 2.5 / mouse 1')
    watch.add_argument('--no-controls', action='store_true',
                       help='Disable local clickable controls and keyboard input')
    watch.add_argument('--minimal', action='store_true',
                       help='Hide the status/buttons; retain local mode, color and quit keys')
    watch.add_argument('--mouse-space', choices=('window','screen'), default='window',
                       help='Aim from this window/pane (default), or use global screen coordinates')
    watch.add_argument('--window-id', type=_integer_range(1),
                       help='Explicit native window ID, normally passed by wall')
    watch.add_argument('--debug-tracking', action='store_true',
                       help='Show face detection, raw normalized coordinates and camera backend')
    watch.add_argument('--invert-x', action='store_true',
                       help='Reverse horizontal movement relative to the selected mode default')
    watch.add_argument('--camera-index', type=_integer_range(0), default=0)
    watch.add_argument('--camera-backend', choices=('auto', 'haar', 'mediapipe'), default='auto')
    watch.add_argument('--seconds', type=_float_range(0, exclusive_low=True),
                       help='Stop after this many seconds (default: until Ctrl+C)')
    preview = sub.add_parser('preview', help='Print one static ANSI frame')
    options(preview)
    preview.add_argument('--look-x', type=_float_range(-1, 1), default=0)
    preview.add_argument('--look-y', type=_float_range(-1, 1), default=0)
    sub.add_parser('themes', help='List the available eye colors')
    add_wall_parser(sub)
    install = sub.add_parser('install-hook', help='Back up zsh startup and enable intro or an opt-in eye pane')
    install.add_argument('--pane', action='store_true',
                         help='Explicitly start the persistent eye launcher in local Terminal.app/iTerm2 shells')
    sub.add_parser('remove-hook', help='Remove the managed intro or pane startup block')
    args = parser.parse_args(argv)
    try:
        if args.cmd == 'intro':
            animate_intro(args)
        elif args.cmd == 'watch':
            animate_watch(args)
        elif args.cmd == 'preview':
            sys.stdout.write(frame(args, Look(args.look_x, args.look_y)))
            sys.stdout.flush()
        elif args.cmd == 'themes':
            for name, description in THEME_DESCRIPTIONS.items():
                print(f'{name:<10} {description}')
        elif args.cmd == 'wall':
            return run_wall(args)
        elif args.cmd == 'install-hook':
            install_hook(True, pane=args.pane)
        elif args.cmd == 'remove-hook':
            install_hook(False)
    except TerminalExit as exc:
        return exc.code
    except KeyboardInterrupt:
        return 0
    except (RuntimeError, ValueError, OSError) as exc:
        print(f'error: {exc}', file=sys.stderr)
        if args.cmd == 'watch' and args.mode == 'camera':
            print('For a camera-free eye, run: terminal-eye watch --mode idle', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
