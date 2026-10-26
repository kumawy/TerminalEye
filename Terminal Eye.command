#!/bin/zsh
# Double-click in Finder, or run this file from Terminal/iTerm2.
set -eu
project_dir="${0:A:h}"
cd "$project_dir"

# Finder's Terminal process can have a shorter PATH than an interactive shell.
# This affects only this launcher and its children.
if ! command -v tmux >/dev/null 2>&1; then
  for brew_bin in /opt/homebrew/bin /usr/local/bin; do
    if [[ -x "$brew_bin/tmux" ]]; then
      export PATH="$brew_bin:$PATH"
      break
    fi
  done
fi

if [[ ! -x "$project_dir/.venv/bin/python" || ! -f "$project_dir/.venv/bin/activate" ]]; then
  print -u2 -- 'Terminal Eye: project environment is missing. Follow the installation steps in README.md.'
  if [[ -t 0 ]]; then
    read -r '?Press Return to close. '
  fi
  exit 1
fi

source "$project_dir/.venv/bin/activate"
export TERMINAL_EYE_DISABLE=1
print -- 'Terminal Eye: click Mouse mode / Face mode / Auto mode to switch.'
print -- 'Keys: m / f / a; q or Ctrl+C stops the eye and releases its camera.'

if command -v tmux >/dev/null 2>&1; then
  print -- 'tmux: shell on the left, eye on the right. Ctrl+B, then an arrow switches panes.'
  exec "$project_dir/scripts/eye_tmux.sh" idle
fi

print -- 'tmux is unavailable: starting in this terminal. Optional two-pane setup: brew install tmux'
exec "$project_dir/.venv/bin/python" -m eyeterm watch --mode idle
