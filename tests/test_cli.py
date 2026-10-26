"""CLI arguments, privacy boundaries, and real PTY lifecycle."""
import errno
import fcntl
import os
from pathlib import Path
import pty
import select
import signal
import struct
import subprocess
import sys
import termios
import time

import pytest

from eyeterm import cli


@pytest.mark.parametrize('arguments', [
    ['preview', '--cell-aspect', 'nan'], ['preview', '--cell-aspect', '0'],
    ['preview', '--width', '0'], ['preview', '--height', '-1'],
    ['watch', '--smoothing', '-.1'], ['watch', '--smoothing', 'nan'],
    ['watch', '--seconds', 'nan'], ['watch', '--seconds', '-1'],
    ['watch', '--fps', '0'], ['intro', '--duration', 'inf'],
])
def test_invalid_numeric_options_fail_during_argument_parsing(arguments):
    with pytest.raises(SystemExit) as error:
        cli.main(arguments)
    assert error.value.code == 2


def test_non_tty_watch_fails_before_opening_camera(capsys):
    assert cli.main(['watch', '--mode', 'camera', '--seconds', '.01']) == 1
    assert 'TTY' in capsys.readouterr().err


def test_tiny_terminal_produces_fallback_instead_of_out_of_bounds_render(monkeypatch, capsys):
    monkeypatch.setattr(cli.shutil, 'get_terminal_size', lambda fallback=(80, 24): os.terminal_size((8, 3)))
    assert cli.main(['preview']) == 0
    text = capsys.readouterr().out
    assert len(text.rstrip('\n')) <= 7
    assert text


def run_in_pty(arguments, *, action=None, job_control=False):
    """Exercise the actual CLI, using a PTY rather than mocking isatty."""
    if arguments and arguments[0]=='watch':
        arguments=[*arguments,'--no-controls']
    master, slave = pty.openpty()
    original_mode = termios.tcgetattr(slave)
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', 14, 42, 0, 0))
    group_options = {}
    if job_control:
        # A child group in the parent's session is not orphaned, so the kernel
        # can actually stop it on SIGTSTP, as it does under an interactive shell.
        group_options = {'process_group': 0} if sys.version_info >= (3, 11) else {'preexec_fn': os.setpgrp}
    process = subprocess.Popen([sys.executable, '-m', 'eyeterm', *arguments], stdin=slave, stdout=slave, stderr=slave,
                               cwd=Path(__file__).resolve().parents[1], start_new_session=not job_control,
                               **group_options)
    captured = bytearray()
    deadline = time.monotonic() + 10
    acted = False
    try:
        while time.monotonic() < deadline:
            if select.select([master], [], [], .05)[0]:
                try:
                    captured.extend(os.read(master, 65536))
                except OSError as exc:
                    if exc.errno != errno.EIO:
                        raise
                    break
            if action and not acted and b'Ctrl+C to exit\x1b[0m\x1b[K' in captured:
                action(process, slave)
                acted = True
            if process.poll() is not None:
                while select.select([master], [], [], .02)[0]:
                    captured.extend(os.read(master, 65536))
                break
        if process.poll() is None:
            process.kill()
            pytest.fail('CLI did not exit within 10 seconds')
        assert termios.tcgetattr(slave) == original_mode
        return process.wait(), bytes(captured)
    finally:
        if process.poll() is None:
            process.kill(); process.wait()
        os.close(master); os.close(slave)


@pytest.mark.parametrize('exit_signal,expected', [(signal.SIGINT, 0), (signal.SIGTERM, 143)])
def test_watch_signal_restores_terminal_in_real_pty(exit_signal, expected):
    code, rendered = run_in_pty(['watch', '--mode', 'idle', '--fps', '12'],
                                action=lambda process, slave: process.send_signal(exit_signal))
    assert code == expected
    assert b'\x1b[?1049h' in rendered
    assert rendered.endswith(b'\x1b[0m\x1b[?25h\x1b[?1049l')
    assert b'Traceback' not in rendered


def test_watch_resizes_to_tiny_pane_and_finishes_without_scroll():
    def resize(process, slave):
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', 3, 8, 0, 0))
        process.send_signal(signal.SIGWINCH)
    code, rendered = run_in_pty(['watch', '--seconds', '.5'], action=resize)
    assert code == 0
    assert b'\n' not in rendered
    assert b'Termina' in rendered
    assert rendered.endswith(b'\x1b[0m\x1b[?25h\x1b[?1049l')


def test_intro_uses_alternate_screen_and_returns_cleanly():
    code, rendered = run_in_pty(['intro', '--duration', '.1'])
    assert code == 0
    assert rendered.startswith(b'\x1b[?1049h\x1b[?25l')
    assert rendered.endswith(b'\x1b[0m\x1b[?25h\x1b[?1049l')
    assert b'\n' not in rendered


def test_camera_notice_precedes_open_and_render_failure_closes_tracker(monkeypatch):
    from io import StringIO
    class Output(StringIO):
        def isatty(self):
            return True
    output, notices = Output(), StringIO()
    monkeypatch.setattr(cli.sys, 'stdout', output)
    monkeypatch.setattr(cli.sys, 'stderr', notices)
    closed = []
    class Camera:
        def __init__(self, index, *, invert_x, backend):
            assert 'No recording or uploads' in notices.getvalue()
            assert (index, invert_x, backend) == (2, False, 'haar')
        def target(self):
            return cli.Look(.5, -.2)
        def close(self):
            closed.append(True)
    def fail_render(*args, **kwargs):
        raise RuntimeError('render failed')
    monkeypatch.setattr('eyeterm.modes.CameraTracker', Camera)
    monkeypatch.setattr(cli, 'frame', fail_render)
    assert cli.main(['watch', '--mode', 'camera', '--camera-index', '2', '--camera-backend', 'haar', '--invert-x']) == 1
    assert closed == [True]
    assert 'render failed' in notices.getvalue()
    assert output.getvalue().endswith('\x1b[0m\x1b[?25h\x1b[?1049l')


def test_camera_failure_explains_idle_alternative_without_silent_fallback(monkeypatch):
    from io import StringIO
    class Output(StringIO):
        def isatty(self):
            return True
    output, notices = Output(), StringIO()
    monkeypatch.setattr(cli.sys, 'stdout', output)
    monkeypatch.setattr(cli.sys, 'stderr', notices)
    def unavailable(*args, **kwargs):
        raise RuntimeError('Camera unavailable: grant access in macOS Privacy & Security > Camera')
    monkeypatch.setattr('eyeterm.modes.CameraTracker', unavailable)
    assert cli.main(['watch', '--mode', 'camera', '--seconds', '.01']) == 1
    assert 'watch --mode idle' in notices.getvalue()
    assert '\x1b[K' not in output.getvalue()


def test_intro_keep_leaves_cursor_below_preview():
    code, rendered = run_in_pty(['intro', '--duration', '.1', '--keep'])
    assert code == 0
    assert b'\x1b[?1049h' not in rendered
    assert rendered.endswith(b'\x1b[13;1H\x1b[K\x1b[0m\x1b[?25h')


def test_watch_actually_suspends_and_resumes_in_real_pty():
    def suspend_and_continue(process, slave):
        process.send_signal(signal.SIGTSTP)
        deadline = time.monotonic() + 3
        try:
            while time.monotonic() < deadline:
                pid, status = os.waitpid(process.pid, os.WNOHANG | os.WUNTRACED)
                if pid:
                    assert os.WIFSTOPPED(status), f'worker exited instead of stopping: {status}'
                    assert os.WSTOPSIG(status) == signal.SIGTSTP
                    break
                time.sleep(.01)
            else:
                pytest.fail('SIGTSTP did not suspend the worker')
        finally:
            process.send_signal(signal.SIGCONT)
    code, rendered = run_in_pty(['watch', '--seconds', '.6'], action=suspend_and_continue, job_control=True)
    assert code == 0
    assert rendered.count(b'\x1b[?1049h\x1b[?25l') == 2
    assert rendered.count(b'\x1b[0m\x1b[?25h\x1b[?1049l') == 2
    assert b'Traceback' not in rendered


@pytest.mark.parametrize('value',['0','-1','nan','inf'])
def test_tracking_gain_rejects_invalid_values(value,capsys):
    with pytest.raises(SystemExit) as error:
        cli.main(['watch','--tracking-gain',value])
    assert error.value.code==2
    assert 'unrecognized arguments' not in capsys.readouterr().err


def test_camera_gain_scales_movement_and_status_shows_raw_detection(monkeypatch):
    from io import StringIO
    from types import SimpleNamespace
    class Output(StringIO):
        def isatty(self):return True
    output=Output()
    monkeypatch.setattr(cli.sys,'stdout',output)
    monkeypatch.setattr(cli.sys,'stderr',StringIO())
    class Camera:
        def __init__(self,*args,**kwargs):pass
        def target(self):return cli.Look(.2,-.1)
        def status(self):
            return SimpleNamespace(state='tracking',backend='mediapipe',look=cli.Look(.2,-.1),age=.01)
        def close(self):pass
    captured=[]
    def render(args,look,*a,**kw):
        captured.append(look)
        return 'eye'
    monkeypatch.setattr('eyeterm.modes.CameraTracker',Camera)
    monkeypatch.setattr(cli,'frame',render)
    assert cli.main(['watch','--mode','camera','--seconds','.02','--smoothing','0',
                     '--tracking-gain','2.5','--debug-tracking'])==0
    assert captured and captured[0]==cli.Look(.5,-.25)
    assert 'FACE FOUND' in output.getvalue()
    assert 'x=+0.20' in output.getvalue() and 'mediapipe' in output.getvalue()


def test_camera_status_distinguishes_no_face_from_centered_face():
    from types import SimpleNamespace
    class Camera:
        state='no-face'
        def status(self):
            return SimpleNamespace(state=self.state,backend='haar',look=cli.Look(0,0),age=.01)
    camera=Camera()
    assert 'NO FACE' in cli.camera_status(camera,False)
    camera.state='tracking'
    assert 'FACE FOUND' in cli.camera_status(camera,False)


def test_camera_diagnostics_outlive_screen_and_capture_release(monkeypatch):
    from io import StringIO
    from types import SimpleNamespace
    import os
    class Output(StringIO):
        def isatty(self):return True
    output=Output();diagnostics=StringIO();events=[]
    monkeypatch.setattr(cli.sys,'stdout',output)
    monkeypatch.setattr(cli.sys,'stderr',diagnostics)
    class Camera:
        def __init__(self,*a,**kw):
            os.write(2,b'INFO: Created TensorFlow Lite XNNPACK delegate for CPU.\n')
        def target(self):return cli.Look(0,0)
        def status(self):return SimpleNamespace(state='tracking',backend='haar',look=cli.Look(0,0),age=0)
        def close(self):
            assert output.getvalue().endswith('\x1b[0m\x1b[?25h\x1b[?1049l')
            os.write(2,b'camera cleanup diagnostic\n')
    monkeypatch.setattr('eyeterm.modes.CameraTracker',Camera)
    monkeypatch.setattr(cli,'frame',lambda *a,**kw:'eye')
    assert cli.main(['watch','--mode','camera','--seconds','.01'])==0
    assert 'TensorFlow' not in diagnostics.getvalue()
    assert 'camera cleanup diagnostic' in diagnostics.getvalue()


def test_watch_view_has_click_targets_below_status_and_reflows(monkeypatch):
    from types import SimpleNamespace
    from eyeterm.trackers import Look
    args=SimpleNamespace(width=None,height=None,style='ascii',cell_aspect=.5,theme='original',mono=True,no_controls=False)
    wide,bar,top=cli.watch_view(args,Look(0,0),1,(80,24),'idle','auto: IDLE')
    assert 'Mouse mode' in wide and 'Face mode' in wide and 'Auto mode' in wide
    assert len(wide.splitlines())<=23
    assert 'auto: IDLE'==wide.splitlines()[top-2]
    assert bar.hit_test(2,top,top_row=top)=='mouse'
    narrow,nbar,ntop=cli.watch_view(args,Look(0,0),1,(34,24),'camera','camera: FACE FOUND')
    assert len(nbar.lines)>len(bar.lines)
    assert len(narrow.splitlines())<=23
    assert ntop<top


def test_watch_mode_click_changes_tracker_without_restarting_process(monkeypatch):
    from io import StringIO
    from types import SimpleNamespace
    from eyeterm.input import InputEvent
    class Output(StringIO):
        def isatty(self):return True
    monkeypatch.setattr(cli.sys,'stdout',Output())
    monkeypatch.setattr(cli.sys,'stderr',StringIO())
    opened=[];closed=[]
    class Camera:
        def __init__(self,*a,**kw):opened.append(True)
        def target(self):return cli.Look(.2,0)
        def status(self):return SimpleNamespace(state='tracking',backend='haar',look=cli.Look(.2,0),age=0)
        def close(self):closed.append(True)
    monkeypatch.setattr('eyeterm.modes.CameraTracker',Camera)
    from eyeterm.controls import build_controls
    bar=build_controls(79,'idle')
    face=next(b for b in bar.buttons if b.mode=='camera')
    auto=next(b for b in bar.buttons if b.mode=='idle')
    events=[[],[InputEvent('click',x=face.left,y=23+face.row,button=0)],
            [InputEvent('click',x=auto.left,y=23+auto.row,button=0)],
            [InputEvent('key',key='q')]]
    drawn=[]
    class Screen:
        def __init__(self,**kwargs):pass
        def __enter__(self):return self
        def __exit__(self,*args):return False
        def size(self):return (80,24)
        def read_events(self):return events.pop(0) if events else [InputEvent('key',key='q')]
        def draw(self,picture,**kwargs):drawn.append(picture)
    monkeypatch.setattr(cli,'TerminalSession',Screen)
    monkeypatch.setattr(cli,'frame',lambda *a,**kw:'\n'.join(['eye']*21)+'\n')
    monkeypatch.setattr(cli.time,'sleep',lambda _:None)
    assert cli.main(['watch','--mode','idle','--seconds','.05'])==0
    assert opened==[True] and closed==[True]
    assert any('FACE FOUND' in text for text in drawn)
    assert any('>Auto mode<' in text for text in drawn)


def test_no_controls_camera_status_never_advertises_inactive_quit_key(monkeypatch):
    from io import StringIO
    from types import SimpleNamespace
    class Output(StringIO):
        def isatty(self):return True
    output=Output()
    monkeypatch.setattr(cli.sys,'stdout',output)
    monkeypatch.setattr(cli.sys,'stderr',StringIO())
    class Camera:
        def __init__(self,*a,**kw):pass
        def target(self):return cli.Look(0,0)
        def status(self):return SimpleNamespace(state='tracking',backend='haar',look=cli.Look(0,0),age=0)
        def close(self):pass
    monkeypatch.setattr('eyeterm.modes.CameraTracker',Camera)
    monkeypatch.setattr(cli,'frame',lambda *a,**kw:'eye')
    assert cli.main(['watch','--mode','camera','--no-controls','--seconds','.01'])==0
    assert 'q quit' not in output.getvalue()
    assert 'Ctrl+C to exit' in output.getvalue()
