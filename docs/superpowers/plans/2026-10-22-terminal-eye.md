# Terminal Eye Implementation Plan

> **For agentic workers:** Use subagent-driven-development or executing-plans to implement each scoped task and verify the combined result.

**Goal:** Improve the existing Terminal Eye to resemble the supplied video frames, with smooth local tracking and reliable macOS terminal cleanup.

**Architecture:** Keep the Python package and pure Pillow/NumPy renderer. Separate animation, terminal lifecycle, trackers and shell hook management; retain existing CLI commands.

**Tech Stack:** Python 3.10+, NumPy, Pillow; optional Quartz, OpenCV and locally available MediaPipe.

**Spec:** CODEX_TASK.md, README.md and assets/reference (all inspected); user explicitly requests implementation and verification.

## Global Constraints

- Default truecolor ASCII; blocks selectable. No external visual assets or runtime network.
- Opt-in camera only; local face center, no recording or uploads.
- Intro hook only through explicit installation. Never edit the actual user's shell during development.
- Separate pane for persistent animation; no background writes over a shell prompt.
- Python 3.10+; restore cursor, colors, tty and alternate screen on every exit path.

## Review Focus

- Very small/resized panes must never scroll or leave stale rows.
- SIGTERM, Ctrl+C, suspend and render errors must restore terminal state and release camera.
- Hook installation must preserve arbitrary existing content including missing trailing newline and quoted environment paths.
- Multiple monitors may have negative origins; horizontal mapping must remain intuitive.
- Camera loss/failure must not retain a stale gaze indefinitely or leak a handle.

### Task 1: Renderer and visual previews (root)

Files: eyeterm/renderer.py, tests/test_renderer.py, scripts/render_previews.py, assets/preview/.
Interfaces: render_eye(width,height,look_x=0,look_y=0,openness=1,*,style='ascii',cell_aspect=.5,theme='original',seed=5921) -> PIL.Image; to_ansi(image,style='ascii',*,colors=True) -> str.
- [x] Add behavior tests for validation, finite inputs, aspect correction, clamp, determinism, themes and independent eyelid clipping; run failing additions.
- [x] Implement cached supersampled skin, curved lids, scleral shadows, radial iris fibers, limbus, pupil and catchlights; distinct material glyphs.
- [x] Save actual encoded ANSI before/after and reproducible rasterizations of those terminal cells; visually inspect and iterate.

### Task 2: Animation and trackers (agent)

Files: eyeterm/animation.py, eyeterm/trackers.py, tests/test_animation.py, tests/test_trackers.py.
Interfaces: Look(x,y); Idle.sample(t), Idle.last; ease(t); Blink.sample(t)->openness; GazeSmoother(smoothing=.12).sample(desired,dt)->Look; CameraTracker(index=0,invert_x=False,backend='auto').target()->Look and close(); mouse_target()->Look.
- [x] Write failing tests for irregular blink timing/range, bounded continuous gaze, negative monitor origins, camera direction/loss/constructor and detector failures.
- [x] Implement time-based motion and global multi-display mouse coordinates. Prefer installed compatible MediaPipe, otherwise local OpenCV Haar, with no model downloads.
- [x] Verify scoped tests and report actual backend/dependency limitations.

### Task 3: CLI and terminal lifecycle (agent)

Files: eyeterm/cli.py, eyeterm/terminal.py, tests/test_terminal.py, tests/test_cli.py.
Interfaces: renderer and animation above; hooks.install_hook(do_install) re-exported by cli for compatibility; hooks.HOOK_START/END.
- [x] Add failing TTY/PTY cleanup, resize, signal, argument and non-TTY tests.
- [x] Add --cell-aspect, --theme, --smoothing, --invert-x, --camera-backend and --seconds; preserve commands. Line diff drawing without scroll, responsive dimensions, signal cleanup/suspend/resume.
- [x] Camera closes on all error paths; visible opt-in notice; no automatic camera fallback. Validate scoped tests.

### Task 4: Hook and tmux reliability (agent)

Files: eyeterm/hooks.py, scripts/eye_tmux.sh, tests/test_hooks.py.
- [x] Add failing tests for exact roundtrip, idempotency, one original backup, malformed markers, venv symlinks, spaces/quotes and safe shell guards.
- [x] Implement reversible managed block preserving bytes, absolute non-resolved venv interpreter and atomic writes. Support ZDOTDIR.
- [x] tmux shell quoting, explicit environment disable, correct pane selection, safe failure cleanup; test with isolated tmux socket/server or mocks.

### Task 5: Combined verification and delivery (root)

Files: README.md (Russian), VERIFICATION.md, pyproject.toml, assets/preview/benchmark.json.
- [x] Run complete pytest suite and CLI/PTY integration; inspect failures before fixing.
- [x] Benchmark render+ANSI at 80x24 and 100x28, capture measured throughput/CPU and actual constraints.
- [x] Check macOS capabilities available here; exercise Terminal.app/iTerm2/mouse/camera as permitted, distinguish simulated from physical verification.
- [x] Independent final code review, fix findings, rerun required checks and package source deliverable with copy-paste install instructions.

Execution completed. Actual native camera access was denied; Terminal.app UI automation was blocked, iTerm2/tmux absent. Simulated/PTy coverage and manual follow-ups are recorded in VERIFICATION.md rather than claiming physical verification.

Camera follow-up 0.2.1: added actual face-presence status, raw tracking diagnostics and configurable gain, captured delayed native stderr to preserve ANSI layout. Verified with 139 passing tests and a native MediaPipe blank-frame smoke; physical face movement still requires user confirmation.
