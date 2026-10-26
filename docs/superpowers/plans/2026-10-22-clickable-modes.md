# Clickable terminal modes implementation plan

> Execution is explicitly requested by the user: configure, verify and implement clickable Mouse mode / Face mode / Auto mode without further setup work from them.

**Goal:** Switch modes live by clicking inside the existing eye pane; keep the command pane usable and make setup concrete.

**Architecture:** TerminalSession owns optional cbreak + mouse reporting, with reversible tty state. Pure control layout/hit testing stays separate from tracker lifetime. A ModeController closes the old camera when leaving Face mode and reports failures visibly without terminating the eye. CLI composes these modules and keeps the renderer unchanged.

**Tech stack:** Existing Python3.10+ stdlib, ANSI SGR/X10 mouse input, tmux3.8 now installed locally.

**Spec:** User's current request; CODEX_TASK.md privacy/cleanup/tmux constraints and prior camera diagnostics.

## Constraints
- Auto mode means idle autonomous motion, not automatic device activation.
- Mouse mode tracks global cursor through Quartz; mouse click input is local to owned foreground eye pane only.
- Face mode opens only on explicit command/Face button/Face shortcut. Leaving it releases camera.
- Camera initialization/failure must be visible; no silent active-mode substitution. Previous working mode remains on failed switch, with message.
- No global config edits. tmux mouse/prefix options scoped to this project's session/window.
- Quit/signals/suspend restore termios, mouse reporting, cursor and screen; click release cannot leak into shell after exit.

## Tasks
- [x] Terminal input: new InputEvent, bounded incremental SGR+X10/key parser; TerminalSession(interactive=False), read_events()->list[InputEvent]. Interactive mode cbreak (ISIG retained), mouse1000+1006; disable/restore on suspend/exit and reenable on resume. Failures restore originals. Test red/green unit+PTY.
- [x] Mode lifetime: ModeController(mode, camera_index=0,invert_x=False,camera_backend='auto'); switch(mode)->bool, target(now)->Look, status(debug=False)->str, close(), .mode, .message. Idle/mouse/camera. No raw native output; root owns NativeDiagnostics for whole interactive watch. Close camera on departure/exception, reject/retain previous mode with message on unavailable target. Test red/green.
- [x] Controls+CLI: exact Mouse mode / Face mode / Auto mode labels, selected state, single-row when enough width and compact stacked layout in narrow panes. Hit-test cell coordinates. Keys m/f/a (1/2/3), q. Mode status separated from buttons. Render auto-sized eye above controls; reflow hit regions on resize. --no-controls permits view-only mode. Preserve finite watch and camera debug/gain options.
- [x] tmux+launch: script session-scoped mouse and prefix; preserve quoting and no global options; project-local double-click launcher. Real isolated tmux socket tests, normal left shell. Validate current user session and refresh only project-owned eye pane after tests.
- [x] Full pytest, real PTY click switching and tty cleanup, isolated real tmux integration and native mouse/camera where allowed; independent review. Update README/VERIFICATION/ZIP and leave usable current session.

## Review focus
Partial escape sequences; malformed/flood mouse input; release/drag outside hit boxes; resized controls; unavailable camera or Quartz during switches; cleanup after native logs/termios/signal failure; ownership of current tmux panes.

Implementation complete: 223 tests passed, physical Face detection/release and real tmux click/prefix integration verified. Builder choice: always create a fresh project-owned tmux session and switch the current client there; this preserves custom prefixes and mouse settings in the original session. GUI Terminal.app mouse events were exercised through the same SGR protocol in PTY, not CUA. No existing user session remained to refresh at delivery; the executable double-click launcher is ready.
