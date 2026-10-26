"""Small, line-diff terminal surface with reversible screen/signal ownership.

Optional local cbreak input keeps Ctrl+C and shell job control working.
Original tty state and mouse reporting are restored on exit and suspension.
"""
from __future__ import annotations

import os
import re
import select
import shutil
import signal
import sys
import termios
from typing import TextIO

from .input import InputEvent, InputParser


_SGR = re.compile(r'\x1b\[[0-9;]*m')


class TerminalExit(SystemExit):
    """A termination signal that should unwind camera and screen contexts."""


def _clip_line(line: str, columns: int) -> str:
    """Crop our ASCII/half-block ANSI rows without cutting an SGR sequence."""
    pieces = []
    cells = 0
    offset = 0
    while offset < len(line) and cells < columns:
        match = _SGR.match(line, offset)
        if match:
            pieces.append(match.group())
            offset = match.end()
        else:
            pieces.append(line[offset])
            cells += 1
            offset += 1
    return ''.join(pieces)


class TerminalSession:
    """Own the visible terminal only while inside a foreground context."""
    def __init__(self, output: TextIO | None = None, *, alternate: bool = True,
                 interactive: bool = False):
        self.output = output if output is not None else sys.stdout
        self.alternate = alternate
        self.interactive = interactive
        self._input_fd: int | None = None
        self._saved_termios: list | None = None
        self._parser = InputParser()
        self._handlers = {}
        self._active = False
        self._lines: list[str] = []
        self._size: tuple[int, int] | None = None
        self._invalidated = False

    def size(self) -> tuple[int, int]:
        try:
            size = os.get_terminal_size(self.output.fileno())
        except (AttributeError, OSError, ValueError):
            size = shutil.get_terminal_size((100, 32))
        return max(1, size.columns), max(1, size.lines)

    def _activate(self):
        if self._active:
            return
        self._active = True
        self._lines = []
        self._size = None
        self.output.write(('\x1b[?1049h' if self.alternate else '') + '\x1b[?25l')
        self.output.flush()
        self._enable_input()

    def _enable_input(self):
        if not self.interactive or self._input_fd is not None:
            return
        try:
            if not sys.stdin.isatty() or not self.output.isatty():
                return
            fd = sys.stdin.fileno()
            if not os.isatty(fd) or not os.isatty(self.output.fileno()):
                return
            original = termios.tcgetattr(fd)
        except (AttributeError, OSError, ValueError, termios.error):
            return
        self._input_fd = fd
        self._saved_termios = original
        self._parser = InputParser()
        current = original[:]
        current[6] = original[6][:]
        current[3] &= ~(termios.ICANON | termios.ECHO)
        # Keep ISIG: Ctrl+C/Ctrl+Z still use normal foreground job signals.
        current[6][termios.VMIN] = 1
        current[6][termios.VTIME] = 0
        termios.tcsetattr(fd, termios.TCSANOW, current)
        self.output.write("\x1b[?1000h\x1b[?1006h\x1b[?2004h")
        self.output.flush()

    def _restore_input(self):
        fd, original = self._input_fd, self._saved_termios
        self._input_fd = None
        self._saved_termios = None
        if fd is None:
            return
        try:
            self.output.write("\x1b[?1000l\x1b[?1006l\x1b[?2004l")
            self.output.flush()
        except (BrokenPipeError, OSError, ValueError):
            pass
        finally:
            # Restore without waiting for output to drain: TCSAFLUSH can
            # block on a slow/disconnected PTY. Flush input afterwards both
            # to discard release bytes and to clear macOS canonical PENDIN.
            try:
                termios.tcsetattr(fd, termios.TCSANOW, original)
            except (OSError, termios.error):
                pass
            try:
                termios.tcflush(fd, termios.TCIFLUSH)
            except (OSError, termios.error):
                pass
        self._parser = InputParser()

    def read_events(self) -> list[InputEvent]:
        """Read only immediately available input from this owned foreground tty."""
        fd = self._input_fd
        if not self._active or fd is None:
            return []
        events = []
        try:
            # Bound work per frame so even a paste/flood cannot stop animation.
            for _ in range(8):
                if not select.select([fd], [], [], 0)[0]:
                    break
                chunk = os.read(fd, 4096)
                if not chunk:
                    break
                events.extend(self._parser.feed(chunk))
        except (InterruptedError, OSError, ValueError):
            pass
        return events

    def _restore(self):
        if not self._active:
            return
        self._active = False
        self._restore_input()
        try:
            self.output.write('\x1b[0m\x1b[?25h' + ('\x1b[?1049l' if self.alternate else ''))
            self.output.flush()
        except (BrokenPipeError, OSError, ValueError):
            # A closed pane has no terminal left to restore.
            pass

    def _interrupt(self, signum, frame):
        raise KeyboardInterrupt

    def _terminate(self, signum, frame):
        raise TerminalExit(128 + signum)

    def _resize(self, signum, frame):
        self._invalidated = True

    def _resume(self, signum, frame):
        self._activate()
        self._invalidated = True

    def _suspend(self, signum, frame):
        self._restore()
        signal.signal(signal.SIGTSTP, signal.SIG_DFL)
        try:
            os.kill(os.getpid(), signal.SIGTSTP)
        finally:
            # Execution resumes here after the shell sends SIGCONT.
            signal.signal(signal.SIGTSTP, self._suspend)
            self._resume(signal.SIGCONT, None)

    def __enter__(self):
        handlers = {signal.SIGINT: self._interrupt, signal.SIGTERM: self._terminate,
                    signal.SIGWINCH: self._resize, signal.SIGTSTP: self._suspend,
                    signal.SIGCONT: self._resume}
        try:
            for signum, handler in handlers.items():
                self._handlers[signum] = signal.getsignal(signum)
                signal.signal(signum, handler)
            self._activate()
        except BaseException:
            self.__exit__(*sys.exc_info())
            raise
        return self

    def draw(self, picture: str, *, status: str | None = None):
        columns, rows = self.size()
        # Reserve the last column; writing there can trigger autowrap/scroll.
        available = max(0, columns - 1)
        lines = [_clip_line(line, available) for line in picture.splitlines()][:rows]
        if status and len(lines) < rows:
            lines.append(_clip_line(status, available))
        size = (columns, rows)
        resized = self._size is not None and self._size != size
        if resized or self._invalidated:
            self.output.write('\x1b[2J')
            self._lines = []
            self._invalidated = False
        pieces = []
        for index in range(min(rows, max(len(lines), len(self._lines)))):
            current = lines[index] if index < len(lines) else ''
            previous = self._lines[index] if index < len(self._lines) else None
            if current != previous:
                pieces.append(f'\x1b[{index + 1};1H{current}\x1b[0m\x1b[K')
        if pieces:
            self.output.write(''.join(pieces))
        self.output.flush()
        self._lines = lines
        self._size = size

    def __exit__(self, exc_type, exc, traceback):
        try:
            self._restore()
        finally:
            for signum, handler in self._handlers.items():
                signal.signal(signum, handler)
            self._handlers.clear()
        return False
