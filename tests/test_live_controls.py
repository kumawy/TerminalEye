"""Actual PTY byte input drives the full CLI; device boundaries are local fakes."""
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

from eyeterm.controls import build_controls


def test_real_pty_clicks_switch_and_release_face_camera_without_restart(tmp_path):
    lifecycle=tmp_path/'camera-lifetime.txt'
    source=f'''
from types import SimpleNamespace
from pathlib import Path
from eyeterm import cli,modes
from eyeterm.trackers import Look
path=Path({str(lifecycle)!r})
def mark(text):
    with path.open('a') as f:f.write(text+'\\n')
class Camera:
    def __init__(self,*a,**kw):mark('OPEN')
    def target(self):return Look(.2,-.1)
    def status(self):return SimpleNamespace(state='tracking',backend='test-device',look=Look(.2,-.1),age=0)
    def close(self):mark('CLOSE')
modes.CameraTracker=Camera
class Mouse:
    def __init__(self,*a,**kw):pass
    def target(self):return Look(.4,0)
    def status(self):return 'window #123'
    def close(self):pass
modes.WindowMouseTracker=Mouse
raise SystemExit(cli.main(['watch','--fps','30','--debug-tracking','--seconds','6']))
'''
    master,slave=pty.openpty()
    original=termios.tcgetattr(slave)
    fcntl.ioctl(slave,termios.TIOCSWINSZ,struct.pack('HHHH',24,80,0,0))
    process=subprocess.Popen([sys.executable,'-c',source],stdin=slave,stdout=slave,stderr=slave,
                             cwd=Path(__file__).resolve().parents[1],start_new_session=True)
    data=bytearray()
    def until(needle,timeout=3):
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            if needle in data:return
            if select.select([master],[],[],.05)[0]:
                try:data.extend(os.read(master,65536))
                except OSError as exc:
                    if exc.errno!=errno.EIO:raise
                    break
            if process.poll() is not None:break
        assert needle in data, f'missing {needle!r}; exit={process.poll()}; tail={bytes(data[-500:])!r}'
    def click(mode):
        button=next(b for b in build_controls(79,'idle').buttons if b.mode==mode)
        os.write(master,f'\x1b[<0;{button.left};{23+button.row}M\x1b[<0;{button.left};{23+button.row}m'.encode())
    try:
        until(b'>Auto mode<')
        assert b'\x1b[?1006h' in data and b'\x1b[?2004h' in data
        # Pasted letters and pasted mouse reports cannot opt into the camera.
        os.write(master,b'\x1b[200~ffq\x1b[<0;17;23M\x1b[201~m')
        until(b'>Mouse mode<')
        assert not lifecycle.exists()
        data.clear();click('camera')
        until(b'camera: FACE FOUND')
        assert lifecycle.read_text().splitlines()==['OPEN']
        assert process.poll() is None
        data.clear();click('idle')
        until(b'>Auto mode<')
        assert lifecycle.read_text().splitlines()==['OPEN','CLOSE']
        assert process.poll() is None
        os.write(master,b'q')
        # Keep draining output: a PTY child may still be finishing its frame.
        until(b'\x1b[?1049l')
        process.wait(timeout=3)
        deadline=time.monotonic()+1
        while time.monotonic()<deadline and select.select([master],[],[],.02)[0]:
            try:data.extend(os.read(master,65536))
            except OSError:break
        assert process.returncode==0
        assert b'\x1b[?1000l\x1b[?1006l\x1b[?2004l' in data
        assert data.endswith(b'\x1b[0m\x1b[?25h\x1b[?1049l')
        assert termios.tcgetattr(slave)==original
        assert lifecycle.read_text().splitlines()==['OPEN','CLOSE']
    finally:
        if process.poll() is None:
            process.kill();process.wait()
        os.close(master);os.close(slave)
