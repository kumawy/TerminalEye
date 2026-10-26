# Multiple eyes following one cursor

The user wants several native Terminal/iTerm2 windows whose eyes each look toward one global cursor from their own position, plus an adjustable aesthetic window grid and color commands. The existing global display normalization gives every eye the same direction, which is the reported bug.

Use stable per-window identity and Quartz window bounds in top-left screen coordinates. Resolve the owning terminal through its tty only when needed; a wall passes window IDs directly. Cache bounds, read the pointer every frame, and keep render input responsive. Existing tmux operation, camera orientation correction, local privacy and safe exit remain. Unknown window identity visibly falls back to screen coordinates, never claims window tracking. Screen mapping remains an explicit option.

Provide `terminal-eye wall`: nine full-eye windows by default, configurable count/columns/gap/margin/display/app. Default Matrix, optional mixed colors. Creating windows must bypass the normal shell startup hook so it cannot recursively create tmux panes. Dry-run must print the layout without opening GUI windows. Owned temporary commands/configuration must safely quote paths and not close unrelated windows.

Add green Matrix, brighter hacker green (latest user request), blue, amber, and violet; retain original/noir/cyber. `themes` lists choices; `watch --theme` and `wall --theme` select them. Local `t` cycles colors; `--minimal` hides the HUD while retaining keyboard controls for wall windows. Respect the user-renamed `ОСЫНЫ АШ.md` and update its commands.

Builder choices: 1..36 windows, auto square grid or explicit columns; 12 fps for wall; menu/Dock-aware usable display bounds; default eye-only wall Mouse mode with no automatic camera. Native GUI automation is unavailable to this tool session, so verify scripts by compilation, controlled external boundaries, dry-run and native Quartz reads; clearly record the GUI validation limit.
