"""Explicit, reversible zsh intro installation; never runs at package import."""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
import shlex
import stat
import sys
import tempfile
import time

HOOK_START = '# >>> terminal-eye startup >>>'
HOOK_END = '# <<< terminal-eye startup <<<'
_METADATA = b'# terminal-eye metadata: '
_METADATA_RE = re.compile(rb'# terminal-eye metadata: separator=([01]) created=([01])\n')


@dataclass(frozen=True)
class _Block:
    start: int
    end: int
    separator: bool = False
    created: bool = False


def _find_block(content: bytes) -> _Block | None:
    """Refuse ambiguous markers instead of guessing which user text to remove."""
    start_marker = HOOK_START.encode()
    end_marker = HOOK_END.encode()
    starts, ends = content.count(start_marker), content.count(end_marker)
    if starts == ends == 0:
        return None
    if starts != 1 or ends != 1:
        raise RuntimeError('Malformed or duplicate Terminal Eye startup block markers; no changes made.')
    start, end = content.find(start_marker), content.find(end_marker)
    if start >= end or (start and content[start - 1:start] != b'\n'):
        raise RuntimeError('Malformed Terminal Eye startup block markers; no changes made.')
    after_start = start + len(start_marker)
    after_end = end + len(end_marker)
    if (content[after_start:after_start + 1] != b'\n'
            or content[end - 1:end] != b'\n'
            or content[after_end:after_end + 1] not in (b'', b'\n')):
        raise RuntimeError('Malformed Terminal Eye startup block markers; no changes made.')
    block_end = after_end + (content[after_end:after_end + 1] == b'\n')
    body = content[after_start + 1:end]
    metadata_lines = [line for line in body.splitlines(keepends=True) if line.startswith(_METADATA)]
    if not metadata_lines:
        # Older releases have no separator metadata. Remove only their marked
        # lines, preserving every surrounding byte rather than guessing whitespace.
        return _Block(start, block_end)
    if len(metadata_lines) != 1 or not (metadata := _METADATA_RE.fullmatch(metadata_lines[0])):
        raise RuntimeError('Malformed Terminal Eye startup block metadata; no changes made.')
    separator, created = (value == b'1' for value in metadata.groups())
    if separator and (not start or content[start - 1:start] != b'\n'):
        raise RuntimeError('Malformed Terminal Eye startup block separator; no changes made.')
    return _Block(start, block_end, separator, created)


def _managed_block(separator: bool, created: bool, *, pane: bool = False) -> bytes:
    guards = (
        'if [[ -o interactive && -t 0 && -t 1 && "${TERM:-dumb}" != dumb\n'
        '      && -z "${TERMINAL_EYE_DISABLE:-}" && -z "${TERMINAL_EYE_ACTIVE:-}"\n'
        '      && -z "${TMUX:-}"'
    )
    if pane:
        launcher = Path(__file__).resolve().parent.parent / 'Terminal Eye.command'
        if not launcher.is_file() or not os.access(launcher, os.X_OK):
            raise RuntimeError(f'Persistent startup launcher is missing or not executable: {launcher}')
        if '\n' in str(launcher) or '\r' in str(launcher):
            raise RuntimeError('Launcher path contains a newline; cannot install a safe shell hook.')
        guards += (
            '\n      && -z "${SSH_CONNECTION:-}" && -z "${SSH_TTY:-}" && -z "${SSH_CLIENT:-}"'
            '\n      && -z "${TERMINAL_EYE_STARTUP_DONE:-}"'
            '\n      && ( "${TERM_PROGRAM:-}" == Apple_Terminal || "${TERM_PROGRAM:-}" == iTerm.app )'
        )
        # Keep this marker inside the current shell, and set it before launching
        # so detach, failure, or repeated `source .zshrc` cannot launch again.
        # Do not exec: the user's ordinary shell must resume after detach/exit.
        action = (
            '  typeset -g TERMINAL_EYE_STARTUP_DONE=1\n'
            '  typeset -g +x TERMINAL_EYE_STARTUP_DONE\n'
            f'  TERMINAL_EYE_ACTIVE=1 {shlex.quote(str(launcher))} || true\n'
        )
    else:
        # abspath deliberately does not resolve symlinks: .venv/bin/python
        # commonly points to a base interpreter, losing the installed environment.
        interpreter = os.path.abspath(sys.executable)
        if '\n' in interpreter or '\r' in interpreter:
            raise RuntimeError('Interpreter path contains a newline; cannot install a safe shell hook.')
        action = (f'  TERMINAL_EYE_ACTIVE=1 {shlex.quote(interpreter)} '
                  '-m eyeterm intro --duration 1.0 2>/dev/null || true\n')
    text = (
        f'{HOOK_START}\n'
        f'# terminal-eye metadata: separator={int(separator)} created={int(created)}\n'
        f'{guards} ]]; then\n'
        f'{action}'
        'fi\n'
        f'{HOOK_END}\n'
    )
    return text.encode('utf-8')


def _file_state(path: Path) -> os.stat_result | None:
    try:
        state = path.lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(state.st_mode):
        raise RuntimeError(f'Shell configuration target is not a regular file: {path}')
    if state.st_uid != os.geteuid():
        raise RuntimeError(f'Shell configuration is owned by another user; refusing to replace it: {path}')
    if state.st_nlink > 1:
        raise RuntimeError(f'Shell configuration has hard links; refusing to replace them: {path}')
    return state


def _ensure_unchanged(path: Path, original: bytes, state: os.stat_result | None) -> None:
    current = _file_state(path)
    if state is None:
        changed = current is not None
    else:
        changed = (current is None or (current.st_dev, current.st_ino, current.st_mtime_ns,
                                       current.st_size) != (state.st_dev, state.st_ino,
                                                           state.st_mtime_ns, state.st_size)
                   or path.read_bytes() != original)
    if changed:
        raise RuntimeError(f'Shell configuration changed during installation; retry: {path}')


def _write_atomic(path: Path, content: bytes, original: bytes,
                  state: os.stat_result | None) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix=f'{path.name}.terminal-eye-tmp-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'wb') as handle:
            os.fchmod(handle.fileno(), stat.S_IMODE(state.st_mode) if state else 0o600)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        _ensure_unchanged(path, original, state)
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _backup(path: Path, content: bytes, mode: int) -> Path:
    # Exclusive creation prevents even a timestamp collision from overwriting a
    # previous backup. Reinstall/remove never call this function.
    backup = path.with_name(f'{path.name}.terminal-eye-backup-{time.time_ns()}')
    descriptor = os.open(backup, os.O_CREAT | os.O_EXCL | os.O_WRONLY, stat.S_IMODE(mode))
    try:
        with os.fdopen(descriptor, 'wb') as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        backup.unlink(missing_ok=True)
        raise
    return backup


def install_hook(do_install: bool, *, pane: bool = False) -> None:
    """Manage an opt-in startup block in $ZDOTDIR/.zshrc (or ~/.zshrc).

    The default is a brief intro; pane=True explicitly chooses the persistent
    launcher, only in local Terminal.app/iTerm2 shells outside tmux.

    First install backs up existing bytes once; no-op reinstall and removal
    create no backups. The managed metadata remembers an inserted newline so
    uninstall can restore a configuration that originally had no final newline.
    User edits outside the managed block remain intact. Symlinks are followed
    once and their regular-file target is atomically replaced, retaining mode.
    """
    requested = Path(os.path.abspath(os.path.expanduser(os.environ.get('ZDOTDIR') or str(Path.home())))) / '.zshrc'
    try:
        # Keep the .zshrc link itself intact, including a dangling link whose
        # target did not exist before installation.
        target = requested.resolve(strict=False)
        state = _file_state(target)
        original = target.read_bytes() if state else b''
        block = _find_block(original)
        if do_install:
            if block:
                content = original[:block.start] + _managed_block(block.separator, block.created, pane=pane) + original[block.end:]
            else:
                separator = bool(original and not original.endswith(b'\n'))
                content = original + (b'\n' if separator else b'') + _managed_block(separator, state is None, pane=pane)
        elif block:
            suffix = original[block.end:]
            # Remove our separator for an exact original-byte roundtrip, but
            # keep it if appended user text would otherwise join the old last
            # command. An existing leading newline already supplies the boundary.
            remove_separator = block.separator and (not suffix or suffix.startswith(b'\n'))
            content = original[:block.start - int(remove_separator)] + suffix
        else:
            print('Startup hook already has the requested state; nothing changed.')
            return
        if content == original:
            print('Startup hook already has the requested state; nothing changed.')
            return
        if do_install and block is None and state:
            _ensure_unchanged(target, original, state)
            backup = _backup(requested, original, state.st_mode)
            print('Backup created:', backup)
        if not do_install and block and block.created and not content:
            _ensure_unchanged(target, original, state)
            target.unlink()
        else:
            _write_atomic(target, content, original, state)
    except (OSError, RuntimeError) as error:
        raise RuntimeError(f'Could not update {requested}: {error}') from error
    print('Startup hook installed.' if do_install else 'Startup hook removed.')
    print('Restart Terminal or run: source ' + shlex.quote(str(requested)))
