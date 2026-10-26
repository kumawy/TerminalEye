"""Exercise temporary shell files and a controlled tmux boundary only."""
from __future__ import annotations

import json
import os
from pathlib import Path
import pty
import shutil
import stat
import subprocess
import sys

import pytest

from eyeterm.hooks import HOOK_END, HOOK_START, install_hook

REAL_PYTHON = sys.executable
PROJECT = Path(__file__).resolve().parents[1]


@pytest.fixture
def shell_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("ZDOTDIR", raising=False)
    return home


@pytest.mark.parametrize("original", [b"", b"# keep me", b"# keep me\n", b"\n\n# comment\n\n", b"alias e='echo x'\r\n# raw \xff\r\n"])
def test_hook_roundtrip_preserves_all_original_bytes(shell_home, original):
    rc = shell_home / ".zshrc"
    rc.write_bytes(original)
    install_hook(True)
    installed = rc.read_bytes()
    install_hook(True)
    assert rc.read_bytes() == installed
    backups = list(shell_home.glob(".zshrc.terminal-eye-backup-*"))
    assert len(backups) == 1
    assert backups[0].read_bytes() == original
    install_hook(False)
    assert rc.read_bytes() == original
    install_hook(False)
    assert len(list(shell_home.glob(".zshrc.terminal-eye-backup-*"))) == 1


def test_hook_removal_preserves_edits_added_after_install(shell_home):
    rc = shell_home / ".zshrc"
    rc.write_bytes(b"# no newline")
    install_hook(True)
    with rc.open("ab") as handle:
        handle.write(b"\n# added later\n")
    install_hook(False)
    assert rc.read_bytes() == b"# no newline\n# added later\n"


def test_hook_created_file_is_removed_only_if_other_content_is_empty(shell_home):
    rc = shell_home / ".zshrc"
    install_hook(False)
    assert not rc.exists()
    install_hook(True)
    install_hook(False)
    assert not rc.exists()
    assert not list(shell_home.glob(".zshrc.terminal-eye-backup-*"))


@pytest.mark.parametrize("content", [
    f"{HOOK_START}\n",
    f"{HOOK_END}\n",
    f"{HOOK_END}\n{HOOK_START}\n",
    f"{HOOK_START}\n{HOOK_END}\n{HOOK_START}\n{HOOK_END}\n",
    f"echo '{HOOK_START}'\n{HOOK_END}\n",
])
@pytest.mark.parametrize("do_install", [True, False])
def test_malformed_markers_refuse_without_modifying_any_files(shell_home, content, do_install):
    rc = shell_home / ".zshrc"
    original = content.encode()
    rc.write_bytes(original)
    with pytest.raises(RuntimeError, match="(?i)(marker|block)"):
        install_hook(do_install)
    assert rc.read_bytes() == original
    assert not list(shell_home.glob(".zshrc.terminal-eye-backup-*"))


def test_hook_respects_zdotdir_and_keeps_symlink_and_permissions(shell_home, tmp_path, monkeypatch):
    dotdir = tmp_path / "zsh config"
    dotdir.mkdir()
    target = tmp_path / "actual config"
    target.write_bytes(b"# original\n\n")
    target.chmod(0o640)
    rc = dotdir / ".zshrc"
    rc.symlink_to(target)
    monkeypatch.setenv("ZDOTDIR", str(dotdir))
    install_hook(True)
    assert rc.is_symlink()
    assert stat.S_IMODE(target.stat().st_mode) == 0o640
    assert not (shell_home / ".zshrc").exists()
    assert len(list(dotdir.glob(".zshrc.terminal-eye-backup-*"))) == 1
    install_hook(False)
    assert rc.is_symlink()
    assert target.read_bytes() == b"# original\n\n"
    assert stat.S_IMODE(target.stat().st_mode) == 0o640


def test_hook_refuses_nonregular_target(shell_home):
    rc = shell_home / ".zshrc"
    rc.mkdir()
    with pytest.raises(RuntimeError, match="(?i)(regular|directory)"):
        install_hook(True)
    assert rc.is_dir()


def make_interpreter(tmp_path):
    log = tmp_path / "python calls.jsonl"
    actual = tmp_path / "actual interpreter"
    actual.write_text("#!/bin/sh\nprintf '%s\\n' \"$0\" >> \"$EYE_HOOK_LOG\"\nprintf '%s\\n' \"$*\" >> \"$EYE_HOOK_LOG\"\n")
    actual.chmod(0o755)
    python = tmp_path / "a venv's bin" / "python"
    python.parent.mkdir()
    python.symlink_to(actual)
    return python, log


def run_hook(rc, env, interactive=True, tty=True):
    flags = ["/bin/zsh", "-f"]
    if interactive:
        flags.append("-i")
    flags += ["-c", 'source "$EYE_RC"; exit 0']
    if tty:
        master, slave = pty.openpty()
        try:
            return subprocess.run(flags, stdin=slave, stdout=slave, stderr=subprocess.PIPE,
                                  env=env, timeout=5)
        finally:
            os.close(slave)
            os.close(master)
    return subprocess.run(flags, input=b"", stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          env=env, timeout=5)


@pytest.mark.parametrize("guard", ["normal", "disabled", "nested", "tmux", "dumb", "noninteractive", "notty"])
def test_hook_runs_only_explicit_intro_in_safe_interactive_shell(shell_home, tmp_path, monkeypatch, guard):
    python, log = make_interpreter(tmp_path)
    monkeypatch.setattr(sys, "executable", str(python))
    install_hook(True)
    rc = shell_home / ".zshrc"
    env = dict(os.environ, EYE_RC=str(rc), EYE_HOOK_LOG=str(log), TERM="xterm-256color")
    for key in ("TERMINAL_EYE_DISABLE", "TERMINAL_EYE_ACTIVE", "TMUX"):
        env.pop(key, None)
    if guard == "disabled": env["TERMINAL_EYE_DISABLE"] = "1"
    if guard == "nested": env["TERMINAL_EYE_ACTIVE"] = "1"
    if guard == "tmux": env["TMUX"] = "/tmp/example,1,0"
    if guard == "dumb": env["TERM"] = "dumb"
    result = run_hook(rc, env, interactive=guard != "noninteractive", tty=guard != "notty")
    assert result.returncode == 0, result.stderr
    if guard == "normal":
        assert log.read_text().splitlines() == [str(python), "-m eyeterm intro --duration 1.0"]
    else:
        assert not log.exists()


@pytest.fixture
def fake_tmux_project(tmp_path):
    repo = tmp_path / "project's terminal eye"
    (repo / "scripts").mkdir(parents=True)
    script = repo / "scripts" / "eye_tmux.sh"
    shutil.copy2(PROJECT / "scripts" / "eye_tmux.sh", script)
    python = repo / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    # Capture the actual interpreter command after zsh has expanded its quoting.
    python.write_text(f"#!{REAL_PYTHON}\nimport os, json, sys\nwith open(os.environ['EYE_RUN_LOG'], 'a') as f: f.write(json.dumps({{'kind':'python', 'argv':sys.argv, 'disable':os.environ.get('TERMINAL_EYE_DISABLE')}})+'\\n')\n")
    python.chmod(0o755)
    shell = tmp_path / "shell's executable"
    shell.write_text(f"#!{REAL_PYTHON}\nimport os,json\nwith open(os.environ['EYE_RUN_LOG'], 'a') as f: f.write(json.dumps({{'kind':'shell','disable':os.environ.get('TERMINAL_EYE_DISABLE'),'venv':os.environ.get('VIRTUAL_ENV'),'path':os.environ.get('PATH')}})+'\\n')\n")
    shell.chmod(0o755)
    bindir = tmp_path / "commands"
    bindir.mkdir()
    tmux = bindir / "tmux"
    tmux.write_text(f'''#!{REAL_PYTHON}
import json, os, subprocess, sys
args = sys.argv[1:]
with open(os.environ['EYE_TMUX_LOG'], 'a') as f:
    f.write(json.dumps(args)+'\\n')
command = args[0]
if command == os.environ.get('EYE_TMUX_FAIL'):
    sys.exit(7)
if command in ('new-session', 'split-window'):
    # Execute the command as tmux's default-shell would; all test programs exit.
    subprocess.run(['/bin/zsh', '-c', args[-1]], check=True)
    print('%101' if command == 'new-session' else '%102')
elif command == 'display-message':
    print('$22' if args[-1] == '#{{session_id}}' else '%77')
''')
    tmux.chmod(0o755)
    tmuxlog = tmp_path / "tmux.jsonl"
    runlog = tmp_path / "programs.jsonl"
    env = dict(os.environ, PATH=str(bindir) + ":" + os.environ.get("PATH", ""),
               SHELL=str(shell), EYE_TMUX_LOG=str(tmuxlog), EYE_RUN_LOG=str(runlog))
    env.pop("TMUX", None)
    env.pop("TMUX_PANE", None)
    env.pop("TERMINAL_EYE_DISABLE", None)
    return script, python, env, tmuxlog, runlog


def json_lines(path):
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


@pytest.mark.parametrize("mode", ["idle", "mouse", "camera"])
def test_tmux_new_session_quotes_paths_and_disables_startup_in_both_programs(fake_tmux_project, mode):
    script, python, env, tmuxlog, runlog = fake_tmux_project
    result = subprocess.run(["/bin/zsh", str(script), mode], env=env, capture_output=True, timeout=20)
    assert result.returncode == 0, result.stderr.decode()
    programs = json_lines(runlog)
    assert programs[0] == {"kind": "shell", "disable": "1", "venv": str(python.parent.parent),
                           "path": str(python.parent) + ":" + env["PATH"]}
    assert programs[1] == {"kind": "python", "argv": [str(python), "-m", "eyeterm", "watch", "--mode", mode], "disable": "1"}
    calls = json_lines(tmuxlog)
    assert next(call for call in calls if call[0] == "split-window")[1:3] == ["-t", "%101"]
    assert ["select-pane", "-t", "%101"] in calls
    assert not any(call[0] == "kill-session" for call in calls)


def test_tmux_inside_existing_session_creates_owned_session_and_switches_client(fake_tmux_project):
    script, python, env, tmuxlog, runlog = fake_tmux_project
    env.update(TMUX="/tmp/server,1,0", TMUX_PANE="%77")
    result = subprocess.run(["/bin/zsh", str(script), "idle"], env=env, capture_output=True, timeout=20)
    assert result.returncode == 0, result.stderr.decode()
    calls = json_lines(tmuxlog)
    assert next(call for call in calls if call[0] == "split-window")[1:3] == ["-t", "%101"]
    assert ["select-pane", "-t", "%101"] in calls
    created = next(call[call.index("-s") + 1] for call in calls if call[0] == "new-session")
    assert ["switch-client", "-t", "=" + created] in calls
    assert not any(call[0] in ("attach-session", "kill-session") for call in calls)
    assert not any("%77" in call for call in calls)
    assert json_lines(runlog)[0]["disable"] == "1"


@pytest.mark.parametrize("nested", [False, True])
def test_tmux_click_controls_and_prefix_are_enabled_only_for_target_session(fake_tmux_project, nested):
    script, python, env, tmuxlog, runlog = fake_tmux_project
    if nested:
        env.update(TMUX="/tmp/server,1,0", TMUX_PANE="%77")
    result = subprocess.run(["/bin/zsh", str(script)], env=env, capture_output=True, timeout=20)
    assert result.returncode == 0, result.stderr.decode()
    calls = json_lines(tmuxlog)
    assert ["set-option", "-t", "$22", "mouse", "on"] in calls
    assert ["set-option", "-t", "$22", "prefix", "C-b"] in calls
    assert not any("-g" in call for call in calls)
    eye = next(program for program in json_lines(runlog) if program["kind"] == "python")
    assert eye["argv"][-2:] == ["--mode", "idle"]


@pytest.mark.parametrize("failure", ["new-session", "split-window", "select-pane", "attach-session"])
def test_tmux_failure_cleans_up_only_session_created_by_this_run(fake_tmux_project, failure):
    script, python, env, tmuxlog, runlog = fake_tmux_project
    env["EYE_TMUX_FAIL"] = failure
    result = subprocess.run(["/bin/zsh", str(script), "idle"], env=env, capture_output=True, timeout=20)
    assert result.returncode != 0
    calls = json_lines(tmuxlog)
    kills = [call for call in calls if call[0] == "kill-session"]
    if failure == "new-session":
        assert kills == []
    else:
        created = next(call[call.index("-s") + 1] for call in calls if call[0] == "new-session")
        assert kills == [["kill-session", "-t", "=" + created]]


@pytest.mark.parametrize("failure", ["split-window", "set-option", "select-pane", "switch-client"])
def test_tmux_existing_session_failure_kills_only_its_new_owned_session(fake_tmux_project, failure):
    script, python, env, tmuxlog, runlog = fake_tmux_project
    env.update(TMUX="/tmp/server,1,0", TMUX_PANE="%77", EYE_TMUX_FAIL=failure)
    result = subprocess.run(["/bin/zsh", str(script), "idle"], env=env, capture_output=True, timeout=20)
    assert result.returncode != 0
    calls = json_lines(tmuxlog)
    created = next(call[call.index("-s") + 1] for call in calls if call[0] == "new-session")
    assert [call for call in calls if call[0] == "kill-session"] == [["kill-session", "-t", "=" + created]]
    assert not any("%77" in call for call in calls)


def test_atomic_replace_failure_leaves_original_and_no_temporary_file(shell_home, monkeypatch):
    rc = shell_home / ".zshrc"
    rc.write_bytes(b"# unchanged\n")
    def fail_replace(*args, **kwargs):
        raise OSError("simulated disk failure")
    monkeypatch.setattr(os, "replace", fail_replace)
    with pytest.raises(RuntimeError, match="simulated disk failure"):
        install_hook(True)
    assert rc.read_bytes() == b"# unchanged\n"
    assert len(list(shell_home.glob(".zshrc.terminal-eye-backup-*"))) == 1
    assert not list(shell_home.glob(".zshrc.terminal-eye-tmp-*"))



def test_tmux_existing_select_failure_does_not_change_original_pane(fake_tmux_project):
    script, python, env, tmuxlog, runlog = fake_tmux_project
    env.update(TMUX="/tmp/server,1,0", TMUX_PANE="%77", EYE_TMUX_FAIL="select-pane")
    result = subprocess.run(["/bin/zsh", str(script), "idle"], env=env, capture_output=True, timeout=20)
    assert result.returncode != 0
    calls = json_lines(tmuxlog)
    created = next(call[call.index("-s") + 1] for call in calls if call[0] == "new-session")
    assert ["kill-session", "-t", "=" + created] in calls
    assert not any(call[0] == "kill-pane" or "%77" in call for call in calls)


def test_hook_refuses_to_replace_a_file_owned_by_another_user(shell_home, monkeypatch):
    rc = shell_home / ".zshrc"
    rc.write_bytes(b"# shared config\n")
    monkeypatch.setattr(os, "geteuid", lambda: rc.stat().st_uid + 1)
    with pytest.raises(RuntimeError, match="(?i)own"):
        install_hook(True)
    assert rc.read_bytes() == b"# shared config\n"
    assert not list(shell_home.glob(".zshrc.terminal-eye-backup-*"))
