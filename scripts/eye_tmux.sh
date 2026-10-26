#!/bin/zsh
# Keep continuous animation in a dedicated pane; never write over shell input.
set -eu
script_dir="${0:A:h}"
repo_dir="${script_dir:h}"
python_bin="$repo_dir/.venv/bin/python"
mode="${1:-idle}"
case "$mode" in idle|mouse|camera) ;; *) echo 'Mode must be idle, mouse, or camera' >&2; exit 2;; esac
if (( $# > 1 )); then
  echo 'Usage: eye_tmux.sh [idle|mouse|camera]' >&2
  exit 2
fi
if ! command -v tmux >/dev/null; then
  echo 'tmux is required: brew install tmux' >&2
  exit 1
fi
if [[ ! -x "$python_bin" ]]; then
  echo "Python venv not found: $python_bin. Run the README setup steps first." >&2
  exit 1
fi

# ${(q)...} is zsh quoting, but tmux may execute with a different default shell.
# ${(qq)...} emits portable single quoting, including embedded apostrophes.
eye_command="exec env TERMINAL_EYE_DISABLE=1 ${(qq)python_bin} -m eyeterm watch --mode ${(qq)mode}"
shell_bin="${SHELL:-/bin/zsh}"
if [[ "$shell_bin" != /* || ! -x "$shell_bin" ]]; then
  shell_bin=/bin/zsh
fi
venv_dir="$repo_dir/.venv"
shell_path="$venv_dir/bin:${PATH:-/usr/bin:/bin}"
# A running tmux server may have an older PATH. Give only this new shell the
# project environment explicitly, so `terminal-eye` works without sourcing it.
shell_command="exec env TERMINAL_EYE_DISABLE=1 VIRTUAL_ENV=${(qq)venv_dir} PATH=${(qq)shell_path} ${(qq)shell_bin} -l"

owned_session=''
cleanup() {
  local result=$?
  if (( result != 0 )) && [[ -n "$owned_session" ]]; then
    tmux kill-session -t "=$owned_session" >/dev/null 2>&1 || true
  fi
  return "$result"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

# Always create an owned session, including from inside tmux. This lets the eye
# enable clicks and Ctrl+B without changing the caller's prefix, mouse settings,
# panes or environment. A failed launch removes only the new session.
session="terminal-eye-${UID}-$$-${RANDOM}"
shell_pane="$(tmux new-session -d -P -F '#{pane_id}' -s "$session" -n terminal -c "$repo_dir" "$shell_command")"
owned_session="$session"
if [[ "$shell_pane" != %<-> ]]; then
  echo 'tmux did not return the newly created shell pane ID.' >&2
  exit 1
fi
session_id="$(tmux display-message -p -t "$shell_pane" '#{session_id}')"
if [[ "$session_id" != '$'<-> ]]; then
  echo 'Cannot identify the Terminal Eye tmux session.' >&2
  exit 1
fi
# These options belong only to the new session. Keep setup at script scope so
# zsh runs the EXIT cleanup if a tmux configuration command fails.
tmux set-option -t "$session_id" mouse on
tmux set-option -t "$session_id" prefix C-b
# -c sets the project directory; explicit env in each shell command works even
# when a pre-existing tmux server has stale inherited environment.
eye_pane="$(tmux split-window -t "$shell_pane" -h -p 50 -P -F '#{pane_id}' -c "$repo_dir" "$eye_command")"
if [[ "$eye_pane" != %<-> ]]; then
  echo 'tmux did not return the newly created eye pane ID.' >&2
  exit 1
fi
tmux select-pane -t "$shell_pane"
if [[ -n "${TMUX:-}" ]]; then
  tmux switch-client -t "=$session"
else
  tmux attach-session -t "=$session"
fi
