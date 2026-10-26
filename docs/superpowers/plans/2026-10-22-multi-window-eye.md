# Multi-window Eye Implementation Plan

> **For agentic workers:** Use subagent-driven-development or executing-plans to implement these independent tasks. Track checks below.

**Goal:** Make eyes look toward the cursor from their own windows, launch adjustable native window grids, and provide color controls.

**Architecture:** A window tracker owns stable identity and cached geometry, independently of rendering. A wall module owns layout/native launch preparation. CLI combines those with deterministic renderer palettes and local input.

**Tech Stack:** Existing Python 3.12, NumPy/Pillow, PyObjC Quartz/AppKit, zsh, native AppleScript.

**Spec:** ../specs/2026-10-22-multi-window-eye-design.md

## Global constraints

- Keep idle/mouse/camera, corrected Face orientation, tmux, opt-in camera and safe exit.
- No GUI control from this session; don't replace an app-control denial with another control tool.
- Canonical /Users/muratovaslan/StudioProjects/TerminalEye; guide filename ОСЫНЫ АШ.md retained.
- No automatic shell configuration changes or unrelated window/session closure.

## Review focus

- Same pointer across distinct windows, moved windows, negative monitor origins and tmux pane offsets.
- Missing window identity/permissions shows honest fallback without blocking frames.
- New windows must not enter the installed startup pane hook before running their eye command.
- Shell/AppleScript path quoting handles spaces, apostrophes, backticks and Unicode.
- Grid count/layout errors fail before opening windows; palettes preserve geometry/detail.

## Tasks

- [x] Window tracking: new window_tracking.py + ModeController integration; tests for geometry, stable identity, fallback, lifecycle and native boundaries.
- [x] Wall: new wall.py/add_parser/run_wall and tests for layout, command handoff, suppression of shell hooks, failures and dry-run; compile generated scripts without executing apps.
- [x] Palettes: renderer THEMES/THEME_DESCRIPTIONS and tests for channel dominance, tonal levels, geometry stability and both output styles.
- [x] CLI: mouse-space/window-id flags, wall/themes commands, minimal HUD and local t cycle; focused behavior tests.
- [x] Documentation/verification: update renamed guide and README, visual palette preview, full suite and isolated native PTY/tmux/Quartz checks, refresh ZIP/version.

For each behavior: add an observable failing test, run it red, implement narrowly, run focused tests green. Root integrates and runs the complete suite once components are ready, reviews boundaries, fixes findings and records actual limits.
