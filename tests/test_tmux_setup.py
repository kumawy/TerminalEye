"""Launchers run against temporary projects; real tmux uses a private socket."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import shlex
import subprocess
import sys
import time
import uuid

import pytest

PROJECT = Path(__file__).resolve().parents[1]


def launcher_project(tmp_path, *, environment=True):
    project = tmp_path / "Terminal Eye's project"
    project.mkdir()
    launcher = project / "Terminal Eye.command"
    # A missing launcher is the failure for this new user entry point.
    if (PROJECT / launcher.name).exists():
        shutil.copy2(PROJECT / launcher.name, launcher)
    if environment:
        bindir = project / ".venv" / "bin"
        bindir.mkdir(parents=True)
        (bindir / "activate").write_text(
            'export VIRTUAL_ENV="${project_dir}/.venv"\n'
            'export PATH="${VIRTUAL_ENV}/bin:/usr/bin:/bin"\n'
        )
        interpreter = bindir / "python"
        interpreter.write_text(
            f"#!{sys.executable}\n"
            "import json, os, sys\n"
            "from pathlib import Path\n"
            "Path(os.environ['EYE_LAUNCH_LOG']).write_text(json.dumps({"
            "'argv':sys.argv, 'venv':os.environ.get('VIRTUAL_ENV'), "
            "'disable':os.environ.get('TERMINAL_EYE_DISABLE')}))\n"
        )
        interpreter.chmod(0o755)
    log = tmp_path / "launch.json"
    env = dict(os.environ, EYE_LAUNCH_LOG=str(log))
    env.pop("TMUX", None)
    return project, launcher, log, env


def test_double_click_launcher_activates_own_venv_and_falls_back_without_tmux(tmp_path):
    project, launcher, log, env = launcher_project(tmp_path)
    result = subprocess.run(["/bin/zsh", str(launcher)], env=env, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr.decode()
    program = json.loads(log.read_text())
    assert program["argv"] == [str(project / ".venv/bin/python"), "-m", "eyeterm", "watch", "--mode", "idle"]
    assert program["venv"] == str(project / ".venv")
    assert program["disable"] == "1"
    assert b"tmux" in result.stdout


def test_double_click_launcher_explains_missing_environment_without_using_system_python(tmp_path):
    project, launcher, log, env = launcher_project(tmp_path, environment=False)
    result = subprocess.run(["/bin/zsh", str(launcher)], env=env, capture_output=True, timeout=10)
    assert result.returncode != 0
    assert b"README" in result.stderr
    assert not log.exists()


def test_double_click_launcher_opens_project_tmux_with_safe_auto_mode(tmp_path):
    project, launcher, log, env = launcher_project(tmp_path)
    # The activate fixture restricts PATH to this temporary venv and system bins.
    fake_tmux = project / ".venv/bin/tmux"
    fake_tmux.write_text("#!/bin/sh\nexit 0\n")
    fake_tmux.chmod(0o755)
    scripts = project / "scripts"
    scripts.mkdir()
    tmux_launcher = scripts / "eye_tmux.sh"
    tmux_launcher.write_text(
        f"#!{sys.executable}\nimport json, os, sys\n"
        "from pathlib import Path\n"
        "Path(os.environ['EYE_LAUNCH_LOG']).write_text(json.dumps({"
        "'args':sys.argv[1:], 'venv':os.environ.get('VIRTUAL_ENV')}))\n"
    )
    tmux_launcher.chmod(0o755)
    result = subprocess.run(["/bin/zsh", str(launcher)], env=env, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr.decode()
    assert json.loads(log.read_text()) == {"args": ["idle"], "venv": str(project / ".venv")}


@pytest.mark.parametrize("split_fails", [False, True])
def test_real_tmux_keeps_existing_session_unchanged_when_creating_owned_session(tmp_path, split_fails):
    tmux = shutil.which("tmux") or ("/opt/homebrew/bin/tmux" if Path("/opt/homebrew/bin/tmux").is_file() else None)
    if tmux is None:
        pytest.skip("tmux not installed")
    socket = "terminal-eye-test-" + uuid.uuid4().hex

    def run_tmux(*args, **kwargs):
        result = subprocess.run([tmux, "-L", socket, *args], text=True, capture_output=True, timeout=10, **kwargs)
        assert result.returncode == 0, result.stderr
        return result.stdout.strip()

    project = tmp_path / "Real tmux's project"
    (project / "scripts").mkdir(parents=True)
    shutil.copy2(PROJECT / "scripts/eye_tmux.sh", project / "scripts/eye_tmux.sh")
    interpreter = project / ".venv/bin/python"
    interpreter.parent.mkdir(parents=True)
    log = tmp_path / "program.json"
    interpreter.write_text(
        f"#!{sys.executable}\nimport json,os,sys,time\n"
        "from pathlib import Path\n"
        f"Path({str(log)!r}).write_text(json.dumps(sys.argv))\n"
        "time.sleep(120)\n"
    )
    interpreter.chmod(0o755)
    eye_executable = interpreter.parent / "terminal-eye"
    eye_executable.write_text("#!/bin/sh\nexit 0\n")
    eye_executable.chmod(0o755)
    commands = tmp_path / "commands"
    commands.mkdir()
    wrapper = commands / "tmux"
    wrapper.write_text(
        '#!/bin/sh\n'
        'if [ "$1" = split-window ] && [ "${EYE_FAIL_SPLIT:-}" = 1 ]; then exit 7; fi\n'
        f'exec "{tmux}" -L "{socket}" "$@"\n'
    )
    wrapper.chmod(0o755)
    env = dict(os.environ, PATH=str(commands) + ":" + os.environ.get("PATH", ""),
               SHELL="/bin/sh", EYE_FAIL_SPLIT="1" if split_fails else "0")
    control_client = None
    try:
        shell_pane = run_tmux("-f", "/dev/null", "new-session", "-d", "-P", "-F", "#{pane_id}", "-s", "terminal-eye-test", "/bin/sleep 120")
        other_pane = run_tmux("new-session", "-d", "-P", "-F", "#{pane_id}", "-s", "unrelated", "/bin/sleep 120")
        run_tmux("set-option", "-g", "mouse", "off")
        run_tmux("set-option", "-g", "prefix", "C-a")
        original_session = run_tmux("display-message", "-p", "-t", shell_pane, "#{session_id}")
        other_session = run_tmux("display-message", "-p", "-t", other_pane, "#{session_id}")
        run_tmux("set-option", "-t", original_session, "mouse", "off")
        run_tmux("set-option", "-t", original_session, "prefix", "C-a")
        control_client = subprocess.Popen([tmux, "-L", socket, "-C", "attach-session", "-t", original_session],
                                          stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        deadline = time.monotonic() + 3
        while not run_tmux("list-clients", "-F", "#{client_session}") and time.monotonic() < deadline:
            time.sleep(0.02)
        server_pid = run_tmux("display-message", "-p", "#{pid}")
        socket_path = run_tmux("display-message", "-p", "#{socket_path}")
        env.update(TMUX=f"{socket_path},{server_pid},0", TMUX_PANE=shell_pane)
        result = subprocess.run(["/bin/zsh", str(project / "scripts/eye_tmux.sh")], env=env, capture_output=True, text=True, timeout=10)
        assert run_tmux("show-option", "-v", "-t", original_session, "mouse") == "off"
        assert run_tmux("show-option", "-v", "-t", original_session, "prefix") == "C-a"
        assert run_tmux("show-option", "-g", "-v", "mouse") == "off"
        assert run_tmux("show-option", "-g", "-v", "prefix") == "C-a"
        assert run_tmux("show-option", "-A", "-v", "-t", other_session, "mouse") == "off"
        assert run_tmux("show-option", "-A", "-v", "-t", other_session, "prefix") == "C-a"
        assert run_tmux("list-panes", "-t", shell_pane, "-F", "#{pane_id}") == shell_pane
        assert run_tmux("list-panes", "-t", other_pane, "-F", "#{pane_id}") == other_pane
        sessions = set(run_tmux("list-sessions", "-F", "#{session_id}").splitlines())
        if split_fails:
            assert result.returncode != 0
            assert sessions == {original_session, other_session}
            assert run_tmux("list-clients", "-F", "#{client_session}") == "terminal-eye-test"
            return
        assert result.returncode == 0, result.stderr
        assert len(sessions) == 3
        own_session = (sessions - {original_session, other_session}).pop()
        assert run_tmux("show-option", "-v", "-t", own_session, "mouse") == "on"
        assert run_tmux("show-option", "-v", "-t", own_session, "prefix") == "C-b"
        own_name = run_tmux("display-message", "-p", "-t", own_session, "#{session_name}")
        assert run_tmux("list-clients", "-F", "#{client_session}") == own_name
        panes = run_tmux("list-panes", "-t", own_session, "-F", "#{pane_id}:#{pane_active}:#{pane_left}").splitlines()
        assert len(panes) == 2
        assert any(pane.endswith(":1:0") for pane in panes)
        left_pane = next(pane.split(":")[0] for pane in panes if pane.endswith(":1:0"))
        shell_lookup = tmp_path / "left shell command.txt"
        run_tmux("send-keys", "-t", left_pane, "command -v terminal-eye > " + shlex.quote(str(shell_lookup)), "Enter")
        deadline = time.monotonic() + 3
        while not shell_lookup.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert shell_lookup.read_text().strip() == str(eye_executable)
        deadline = time.monotonic() + 3
        while not log.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert json.loads(log.read_text()) == [str(interpreter), "-m", "eyeterm", "watch", "--mode", "idle"]
    finally:
        subprocess.run([tmux, "-L", socket, "kill-server"], capture_output=True, timeout=10)
        if control_client is not None:
            try:
                control_client.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                control_client.kill()
                control_client.communicate(timeout=5)
