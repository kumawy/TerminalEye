"""Opt-in persistent startup with real zsh guards and temporary dotfiles."""
from __future__ import annotations

import json
import os
from pathlib import Path
import pty
import subprocess
import sys

import pytest

from eyeterm import cli, hooks

REAL_PYTHON = sys.executable


@pytest.fixture
def pane_home(tmp_path, monkeypatch):
    home = tmp_path / 'home'
    home.mkdir()
    monkeypatch.setenv('HOME', str(home))
    monkeypatch.delenv('ZDOTDIR', raising=False)
    root = tmp_path / "project's Terminal Eye"
    (root / 'eyeterm').mkdir(parents=True)
    launcher = root / 'Terminal Eye.command'
    launcher.write_text(f'''#!{REAL_PYTHON}
import json, os, sys
with open(os.environ['EYE_LAUNCH_LOG'], 'a') as handle:
    handle.write(json.dumps({{'argv': sys.argv, 'active': os.environ.get('TERMINAL_EYE_ACTIVE'),
                              'done': os.environ.get('TERMINAL_EYE_STARTUP_DONE')}})+'\\n')
sys.exit(int(os.environ.get('EYE_EXIT_STATUS', '0')))
''')
    launcher.chmod(0o755)
    monkeypatch.setattr(hooks, '__file__', str(root / 'eyeterm' / 'hooks.py'))
    return home, launcher


def pane_environment(home):
    environment = dict(os.environ, HOME=str(home), EYE_RC=str(home / '.zshrc'),
                       EYE_LAUNCH_LOG=str(home / 'launch.jsonl'),
                       EYE_AFTER_LOG=str(home / 'after.txt'), TERM='xterm-256color',
                       TERM_PROGRAM='Apple_Terminal')
    for name in ('TERMINAL_EYE_DISABLE', 'TERMINAL_EYE_ACTIVE', 'TERMINAL_EYE_STARTUP_DONE',
                 'TMUX', 'SSH_CONNECTION', 'SSH_TTY', 'SSH_CLIENT'):
        environment.pop(name, None)
    return environment


def source_pane_hook(environment, *, interactive=True, tty=True, allexport=False):
    command = ['/bin/zsh', '-f'] + (['-i'] if interactive else [])
    # A launcher returning/detaching must return to this same usable shell.
    command += ['-c', ('setopt allexport; ' if allexport else '') + 'source "$EYE_RC"; source "$EYE_RC"; '
                      'print -r -- "shell alive:${TERMINAL_EYE_STARTUP_DONE:-}" > "$EYE_AFTER_LOG"; exit 0']
    if tty:
        master, slave = pty.openpty()
        try:
            return subprocess.run(command, env=environment, stdin=slave, stdout=slave,
                                  stderr=subprocess.PIPE, timeout=10)
        finally:
            os.close(slave)
            os.close(master)
    return subprocess.run(command, env=environment, input=b'', capture_output=True, timeout=10)


def launches(home):
    log = home / 'launch.jsonl'
    return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []


@pytest.mark.parametrize('terminal', ['Apple_Terminal', 'iTerm.app'])
@pytest.mark.parametrize('exit_status', ['0', '7'])
def test_pane_hook_launches_once_with_quoted_path_and_returns_to_normal_shell(pane_home, terminal, exit_status):
    home, launcher = pane_home
    hooks.install_hook(True, pane=True)
    environment = pane_environment(home)
    environment.update(TERM_PROGRAM=terminal, EYE_EXIT_STATUS=exit_status)
    result = source_pane_hook(environment)
    assert result.returncode == 0, result.stderr.decode()
    assert launches(home) == [{'argv': [str(launcher)], 'active': '1', 'done': None}]
    assert (home / 'after.txt').read_text() == 'shell alive:1\n'


@pytest.mark.parametrize('guard', [
    'disabled', 'active', 'tmux', 'ssh_connection', 'ssh_tty', 'ssh_client',
    'dumb', 'missing_term', 'other_app', 'missing_app', 'done', 'noninteractive', 'notty',
])
def test_pane_hook_excludes_unsafe_or_repeated_startup(pane_home, guard):
    home, launcher = pane_home
    hooks.install_hook(True, pane=True)
    environment = pane_environment(home)
    assignments = {
        'disabled': ('TERMINAL_EYE_DISABLE', '1'), 'active': ('TERMINAL_EYE_ACTIVE', '1'),
        'tmux': ('TMUX', '/tmp/tmux,1,0'), 'ssh_connection': ('SSH_CONNECTION', 'host 1 host 2'),
        'ssh_tty': ('SSH_TTY', '/dev/ttys001'), 'ssh_client': ('SSH_CLIENT', 'host 1 2'),
        'dumb': ('TERM', 'dumb'), 'other_app': ('TERM_PROGRAM', 'vscode'),
        'done': ('TERMINAL_EYE_STARTUP_DONE', '1'),
    }
    if guard in assignments:
        name, value = assignments[guard]
        environment[name] = value
    if guard == 'missing_term': environment.pop('TERM')
    if guard == 'missing_app': environment.pop('TERM_PROGRAM')
    result = source_pane_hook(environment, interactive=guard != 'noninteractive', tty=guard != 'notty')
    assert result.returncode == 0, result.stderr.decode()
    assert launches(home) == []
    assert (home / 'after.txt').is_file()


@pytest.mark.parametrize('original', [b'', b'# no final newline', b'\n# custom\n\n', b'# raw \xff\r\n'])
def test_pane_hook_install_reinstall_and_removal_restore_original_bytes_once(pane_home, original):
    home, launcher = pane_home
    startup = home / '.zshrc'
    startup.write_bytes(original)
    hooks.install_hook(True, pane=True)
    installed = startup.read_bytes()
    hooks.install_hook(True, pane=True)
    assert startup.read_bytes() == installed
    assert installed.count(hooks.HOOK_START.encode()) == 1
    hooks.install_hook(False)
    assert startup.read_bytes() == original
    backups = list(home.glob('.zshrc.terminal-eye-backup-*'))
    assert len(backups) == 1
    assert backups[0].read_bytes() == original


def test_changing_startup_mode_keeps_one_original_backup_and_external_content(pane_home):
    home, launcher = pane_home
    startup = home / '.zshrc'
    startup.write_bytes(b'# user config')
    hooks.install_hook(True)
    intro = startup.read_bytes()
    hooks.install_hook(True, pane=True)
    assert startup.read_bytes() != intro
    with startup.open('ab') as handle:
        handle.write(b'# user addition\n')
    hooks.install_hook(True)
    assert startup.read_bytes() == intro + b'# user addition\n'
    hooks.install_hook(False)
    assert startup.read_bytes() == b'# user config\n# user addition\n'
    assert len(list(home.glob('.zshrc.terminal-eye-backup-*'))) == 1


@pytest.mark.parametrize('pane', [False, True])
def test_cli_install_hook_flag_installs_requested_startup_mode(pane_home, pane):
    home, launcher = pane_home
    assert cli.main(['install-hook'] + (['--pane'] if pane else [])) == 0
    startup = (home / '.zshrc').read_text()
    if pane:
        environment = pane_environment(home)
        result = source_pane_hook(environment)
        assert result.returncode == 0, result.stderr.decode()
        assert launches(home)[0]['argv'] == [str(launcher)]
    else:
        assert '-m eyeterm intro --duration 1.0' in startup
        assert str(launcher) not in startup
    assert cli.main(['remove-hook']) == 0
    assert not (home / '.zshrc').exists()


def test_pane_install_missing_launcher_leaves_shell_file_unchanged(pane_home):
    home, launcher = pane_home
    startup = home / '.zshrc'
    startup.write_bytes(b'# user settings\n')
    launcher.unlink()
    with pytest.raises(RuntimeError, match='(?i)launcher'):
        hooks.install_hook(True, pane=True)
    assert startup.read_bytes() == b'# user settings\n'
    assert not list(home.glob('.zshrc.terminal-eye-backup-*'))



def test_pane_removal_keeps_appended_assignment_separate_from_original_unterminated_line(pane_home):
    home, launcher = pane_home
    startup = home / '.zshrc'
    startup.write_bytes(b'export EYE_FIRST=one')
    hooks.install_hook(True, pane=True)
    with startup.open('ab') as handle:
        handle.write(b'export EYE_SECOND=two\n')
    hooks.install_hook(False)
    result = subprocess.run(
        ['/bin/zsh', '-f', '-c', 'source "$EYE_RC"; print -r -- "${EYE_FIRST}|${EYE_SECOND}"'],
        env=dict(os.environ, HOME=str(home), EYE_RC=str(startup)), capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr.decode()
    assert result.stdout == b'one|two\n'
    assert startup.read_bytes() == b'export EYE_FIRST=one\nexport EYE_SECOND=two\n'



def test_pane_startup_done_stays_shell_local_with_allexport(pane_home):
    home, launcher = pane_home
    hooks.install_hook(True, pane=True)
    result = source_pane_hook(pane_environment(home), allexport=True)
    assert result.returncode == 0, result.stderr.decode()
    assert launches(home) == [{'argv': [str(launcher)], 'active': '1', 'done': None}]
    assert (home / 'after.txt').read_text() == 'shell alive:1\n'
