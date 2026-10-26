"""Terminal lifecycle and drawing contracts, independent of eye rendering."""
import io
import os
import signal

import pytest

from eyeterm import terminal


class TTYOutput(io.StringIO):
    def isatty(self):
        return True


def test_line_diff_never_emits_newline_and_clears_changed_rows(monkeypatch):
    monkeypatch.setattr(terminal.shutil, 'get_terminal_size', lambda fallback=(80, 24): os.terminal_size((30, 12)))
    output = TTYOutput()
    with terminal.TerminalSession(output) as screen:
        output.seek(0); output.truncate(0)
        screen.draw('abc\ndef\n')
        assert output.getvalue() == '\x1b[1;1Habc\x1b[0m\x1b[K\x1b[2;1Hdef\x1b[0m\x1b[K'
        output.seek(0); output.truncate(0)
        screen.draw('abc\nXYZ\n')
        assert output.getvalue() == '\x1b[2;1HXYZ\x1b[0m\x1b[K'
        output.seek(0); output.truncate(0)
        screen.draw('abc\n')
        assert output.getvalue() == '\x1b[2;1H\x1b[0m\x1b[K'


def test_resize_invalidates_diff_without_drawing_outside_screen(monkeypatch):
    size = [30, 12]
    monkeypatch.setattr(terminal.shutil, 'get_terminal_size', lambda fallback=(80, 24): os.terminal_size(size))
    output = TTYOutput()
    with terminal.TerminalSession(output) as screen:
        screen.draw('abc\ndef\n')
        output.seek(0); output.truncate(0)
        size[:] = [10, 1]
        screen.draw('small\nignored\n')
        rendered = output.getvalue()
        assert '\x1b[1;1Hsmall' in rendered
        assert '\x1b[2;' not in rendered
        assert '\n' not in rendered


def test_exception_restores_cursor_screen_and_previous_signal_handlers():
    output = TTYOutput()
    previous = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGWINCH, signal.SIGTSTP, signal.SIGCONT)}
    with pytest.raises(RuntimeError, match='render failed'):
        with terminal.TerminalSession(output):
            raise RuntimeError('render failed')
    assert output.getvalue().endswith('\x1b[0m\x1b[?25h\x1b[?1049l')
    assert all(signal.getsignal(sig) == handler for sig, handler in previous.items())


def test_sigterm_is_a_normal_exit_that_still_cleans_up():
    output = TTYOutput()
    with pytest.raises(terminal.TerminalExit) as interrupted:
        with terminal.TerminalSession(output):
            signal.raise_signal(signal.SIGTERM)
    assert interrupted.value.code == 143
    assert output.getvalue().endswith('\x1b[0m\x1b[?25h\x1b[?1049l')


def test_suspend_restores_then_resume_redraws_without_losing_handlers(monkeypatch):
    monkeypatch.setattr(terminal.shutil, 'get_terminal_size', lambda fallback=(80, 24): os.terminal_size((30, 12)))
    output = TTYOutput()
    real_kill = terminal.os.kill
    def resume_instead_of_stopping(pid, signum):
        assert pid == os.getpid() and signum == signal.SIGTSTP
        assert output.getvalue().endswith('\x1b[0m\x1b[?25h\x1b[?1049l')
        real_kill(pid, signal.SIGCONT)
    monkeypatch.setattr(terminal.os, 'kill', resume_instead_of_stopping)
    with terminal.TerminalSession(output) as screen:
        screen.draw('eye')
        output.seek(0); output.truncate(0)
        signal.raise_signal(signal.SIGTSTP)
        screen.draw('eye')
        assert '\x1b[?1049h\x1b[?25l' in output.getvalue()
        assert '\x1b[1;1Heye' in output.getvalue()
        assert signal.getsignal(signal.SIGTSTP) == screen._suspend


@pytest.fixture
def tty_pair(monkeypatch):
    import pty
    master, slave = pty.openpty()
    input_stream = os.fdopen(os.dup(slave), 'r', buffering=1)
    output_stream = os.fdopen(os.dup(slave), 'w', buffering=1)
    monkeypatch.setattr(terminal.sys, 'stdin', input_stream)
    try:
        yield master, slave, output_stream
    finally:
        input_stream.close()
        output_stream.close()
        os.close(master)
        os.close(slave)


def test_interactive_session_preserves_signals_and_restores_tty_on_exception(tty_pair):
    import termios
    master, slave, output = tty_pair
    original = termios.tcgetattr(slave)
    with pytest.raises(RuntimeError):
        with terminal.TerminalSession(output, interactive=True):
            active = termios.tcgetattr(slave)
            assert not active[3] & (termios.ICANON | termios.ECHO)
            assert active[3] & termios.ISIG == original[3] & termios.ISIG
            raise RuntimeError('draw failed')
    assert termios.tcgetattr(slave) == original
    rendered = os.read(master, 4096).decode()
    assert '\x1b[?1000h\x1b[?1006h' in rendered
    assert rendered.index('\x1b[?1000l') < rendered.index('\x1b[?25h')
    assert '\x1b[?1006l' in rendered


def test_read_events_uses_local_tty_and_is_nonblocking(tty_pair):
    import select
    from eyeterm.input import InputEvent
    master, slave, output = tty_pair
    with terminal.TerminalSession(output, interactive=True) as screen:
        assert screen.read_events() == []
        os.write(master, b'f\x1b[<0;9;10M')
        assert select.select([slave], [], [], 1)[0]
        assert screen.read_events() == [InputEvent('key', key='f'), InputEvent('click', x=9, y=10, button=0)]
        assert screen.read_events() == []
    assert screen.read_events() == []


def test_noninteractive_or_redirected_stream_keeps_input_unchanged(tty_pair):
    import termios
    master, slave, output = tty_pair
    original = termios.tcgetattr(slave)
    with terminal.TerminalSession(output) as screen:
        assert termios.tcgetattr(slave) == original
        assert screen.read_events() == []
    with terminal.TerminalSession(io.StringIO(), interactive=True) as screen:
        assert termios.tcgetattr(slave) == original
        assert screen.read_events() == []


def test_suspend_restores_tty_then_resume_reenables_input(tty_pair, monkeypatch):
    import termios
    master, slave, output = tty_pair
    original = termios.tcgetattr(slave)
    def resume_instead_of_stopping(pid, signum):
        assert termios.tcgetattr(slave) == original
    monkeypatch.setattr(terminal.os, 'kill', resume_instead_of_stopping)
    with terminal.TerminalSession(output, interactive=True) as screen:
        screen._suspend(signal.SIGTSTP, None)
        assert not termios.tcgetattr(slave)[3] & termios.ICANON
        assert screen.read_events() == []
    assert termios.tcgetattr(slave) == original


def test_exit_discards_queued_mouse_release(tty_pair):
    import select
    master, slave, output = tty_pair
    with terminal.TerminalSession(output, interactive=True):
        os.write(master, b'\x1b[<0;9;10m')
        assert select.select([slave], [], [], 1)[0]
    assert not select.select([slave], [], [], 0)[0]


def test_enter_failure_restores_tty_and_handlers(tty_pair):
    import termios
    master, slave, output = tty_pair
    original = termios.tcgetattr(slave)
    previous = signal.getsignal(signal.SIGINT)
    class BrokenOutput:
        def isatty(self):
            return True
        def fileno(self):
            return output.fileno()
        def write(self, text):
            raise OSError('closed terminal')
        def flush(self):
            pass
    with pytest.raises(OSError):
        with terminal.TerminalSession(BrokenOutput(), interactive=True):
            pass
    assert termios.tcgetattr(slave) == original
    assert signal.getsignal(signal.SIGINT) == previous


def test_interactive_session_guards_paste_and_disables_it_on_exit(tty_pair):
    master, slave, output = tty_pair
    with terminal.TerminalSession(output, interactive=True):
        pass
    rendered = os.read(master, 4096).decode()
    assert '\x1b[?2004h' in rendered
    assert '\x1b[?2004l' in rendered
    assert rendered.index('\x1b[?2004l') < rendered.index('\x1b[?25h')


def test_closed_output_still_restores_tty_and_signal_handlers(tty_pair):
    import termios
    master, slave, output = tty_pair
    original = termios.tcgetattr(slave)
    previous = signal.getsignal(signal.SIGINT)
    with terminal.TerminalSession(output, interactive=True):
        output.close()
    assert termios.tcgetattr(slave) == original
    assert signal.getsignal(signal.SIGINT) == previous
