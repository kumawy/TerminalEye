# Codex task · Terminal Eye for macOS (приоритет: визуальная точность + надёжность)

Ты — senior macOS engineer, Python TUI engineer и technical artist. **Не создавай новый проект с нуля: открой и доработай этот репозиторий.** В репозитории уже есть работающий программный прототип, инструкции, тесты и кадры-референсы из предоставленного пользователем видео.

## 0. Самое важное

Пользователь хочет **глаз в терминале, визуально максимально близкий к референсу**. Это не emoji-глаз и не простой рисунок из трёх символов. На видео окно заполнено тысячами мелких символов с градиентной 24-bit окраской; почти реалистичный человеческий глаз открывается, затем смотрит по сторонам. Цвета: тёплые коричневые веки и кожа, светлая склера, зелёно-янтарная радужка, чёрный зрачок. Есть ощущение плотного живого ASCII-поля.

Посмотри `assets/reference/reference_sequence.jpg` и по отдельности `assets/reference/eye_*.png`. **Не утверждай, что текущий MVP полностью совпадает с референсом**: он упрощён. В приоритете именно улучшение качества картинки и поведения, а не добавление множества UI-функций.

## 1. Целевое поведение

- `terminal-eye intro`: при запуске Terminal/iTerm2 глаз плавно открывается за ~1 сек, слегка фокусируется, затем возвращает управление обычной оболочке. Не оставляй ввод команд сломанным или терминал без курсора.
- `terminal-eye watch --mode idle`: живой глаз постоянно и естественно смотрит по сторонам, моргает не по часам, имеет microsaccades, инерцию и паузы.
- `terminal-eye watch --mode mouse`: наблюдает **за глобальным курсором**, даже вне терминала, по возможности с учётом нескольких мониторов. `Quartz.CGEventCreate(None)`, `CGEventGetLocation()`, `CGGetActiveDisplayList()` либо другой надёжный native API. Не путать со вводом координат курсора внутри TUI.
- `terminal-eye watch --mode camera`: отслеживает **положение лица** в кадре в реальном времени, двигает зрачок в ответ. Предпочтительно MediaPipe Face Detection/Face Mesh, OpenCV Haar можно оставить fallback. Вебкамера строго opt-in, никакой фоновой съёмки/сохранения/отправки видео.
- `terminal-eye watch --style ascii|blocks`: переключение плотного ANSI color ASCII и Unicode half-blocks. Приоритет default ASCII, близкий к видео.
- `./scripts/eye_tmux.sh idle|mouse|camera`: отдельная панель с глазом + нормальная оболочка для команд, чтобы терминал оставался usable.

## 2. Визуальные требования (P0)

Доработай `eyeterm/renderer.py`. Текущая реализация процедурная, но выглядит упрощённо: ровная белая область и слишком простые контуры. Хочу ближе к исходному ролику:

- Эллипсоидальное широкое человеческое глазное отверстие с естественной геометрией век (spline/Bezier, особенно при моргании), складки верхнего/нижнего века, мягкие тени и фактуру кожи.
- Светлая, но не чисто-белая склера; естественные мягкие тени, которые зависят от века, положение радужки под веком.
- Круглая радужка: зелёный и тёплый янтарный рисунок, неоднородные радиальные волокна, тёмное лимбальное кольцо, чёрный зрачок, небольшой спекулярный блик.
- При взгляде в стороны смещаются радужка и зрачок; они корректно обрезаются веком. Веко **не путешествует** за радужкой. При моргании подвижные веки закрывают радужку (не просто исчезают пиксели).
- Статическая (seeded) текстура без визуального дрожания при каждом кадре, плюс минимальная контролируемая динамика. Не делай фотошум чрезмерным.
- Символьная палитра с разными текстурами для кожи/склеры/радужки, соответствующими референсу. Используй ANSI 24-bit fg и при необходимости bg, символьные градиенты. Сохранить внешний вид в стандартном Terminal.app и iTerm2.
- Корректный aspect ratio для monospace cell (ширина символа около половины высоты), независимый от реального разрешения монитора. Пользователь должен иметь возможность настроить `--cell-aspect`.
- Минимизируй flicker, применяй курсорное позиционирование/двойную буферизацию/строчный diff или иной метод, не спамящий бесконечный scrollback. Целевая частота 12–20 FPS на обычном MacBook; заранее профилируй, не обещай 60 FPS.
- 3 темы: `original` (из видео, default), `noir` (чёрно-белый тревожный глаз), `cyber` (тёмная кожа с бирюзовой радужкой), но это **P2**, после визуальной точности original.

## 3. Переходы и интеракции (P0/P1)

- Idle target: piecewise-random targets with nonuniform dwell times (0.7–3 s), smoothstep/ease-out, rare short quick saccades, no constant sinusoidal oscillation.
- Blinks: irregular time intervals around 3–7 s, quick closing, slightly slower opening, optional half-blink, no blinking every exact N seconds.
- Mouse: smoothing (configurable), normalized pointer across all monitors. If pointer goes to far right, iris goes to right; calibrate user-facing horizontal direction.
- Camera: center-of-face tracking; user permission visible; move the eye in the corresponding screen direction, not inverted unexpectedly. Optional horizontal invert flag for differing webcams. Camera unavailable => explain how to grant access and offer idle mode, never silently use camera.
- On loss of tracking: gracefully return gaze to center or idle.
- Ctrl+C/SIGTERM: always cleanup camera handle, restore ANSI cursor and terminal mode. Ctrl+Z, resize (SIGWINCH), shell suspend/resume ideally handled.
- Optional runtime hotkeys `i` idle, `m` mouse, `c` camera, `q` quit, only **inside dedicated pane**; camera must not start without clear user action and camera permission.

## 4. Integration and security (P0)

- macOS zsh (`.zshrc`), Terminal.app, iTerm2.
- Default `install-hook`: **only intro**, and only with an explicit installation command. No unrequested modifications.
- Backup `.zshrc` before changes; block-marked, idempotent install, clean uninstall with no side effects on user's other configs. Preserve quotes, comments, existing shell customizations. Never overwrite existing files without backup.
- Store absolute path to project environment executable, so startup still works before `source .venv/bin/activate`.
- Don't run intro in non-interactive shells and allow `TERMINAL_EYE_DISABLE=1` escape hatch.
- Continuous live eye in **a separate tmux split pane** or separately launched terminal window. Never implement a background process which constantly writes into the *same* shell prompt, destroying command entry.
- Optional persistent auto-pane launch at Terminal startup may be offered **only through an explicit opt-in flag** with reversible setup and anti-recursion safeguards. Explain limitations first.
- No global keyboard logging, click injection, mic use, screen recording, secret autostart, or video uploads. Track only *coordinate of cursor* or *local face position*.
- Dependencies local; no network needed at runtime. Do not pull external assets unless user explicitly approves licenses.

## 5. Architecture

Maintain package files but refactor if useful:

```
eyeterm/
    cli.py              # CLI commands, settings, lifecycle
    renderer.py         # Pure visual renderer + ASCII/ANSI frame encoding
    animation.py        # Blink and gaze state machines
    trackers.py         # mouse / camera / idle providers
    terminal.py         # frame diff, resizing, raw keyboard, cursor lifecycle
    config.py           # persistent optional config, non-sensitive
scripts/
    eye_tmux.sh         # terminal split pane launcher
assets/
    reference/          # original frames, read-only
    preview/            # generated before/after output
 tests/
 README.md
 CODEX_TASK.md
 pyproject.toml
```

Avoid a heavyweight UI framework unless it clearly improves latency or state management. Python 3.10+ baseline; document actual macOS verification. MediaPipe Python wheels vary with architecture/version: inspect supported packages/versions *before pinning*; keep OpenCV fallback.

## 6. Test plan and actual verification

1. `python -m pytest -q` must pass; add tests for blinking range, gaze clamp, aspect ratio, deterministic texture, ANSI frame reset, invalid sizes, handler failures.
2. Create before/after terminal-screenshot previews for ASCII and blocks in `assets/preview/`. Visual comparison against `assets/reference/`.
3. `terminal-eye --help`, `intro`, `preview`, `watch idle`, `watch mouse`, `watch camera` each verified where the system permits. If Mac/camera unavailable, explicitly mark tests not performed rather than invent results.
4. Simulate `Ctrl+C` and assert alternate-screen exit/restored cursor. Simulate resize where feasible.
5. Ensure camera mode uses camera only *after explicit command* and releases handle, even when exception occurs.
6. On an actual Mac, check both Terminal.app and iTerm2, light/dark appearance, multiple monitors for pointer tracking.
7. `install-hook`/`remove-hook` on a temp HOME: preserve unrelated `.zshrc` content and verify exactly one managed block and one backup.
8. Record sample performance at 80x24 and 100x28, state actual FPS and CPU where measured.

## 7. Final delivery

- Working, tested project in-place (do not replace with just a design or pseudocode).
- Clear README in **Russian** and install/launch commands ready for copy-paste.
- At least one screenshot/preview of actual generated ANSI art, plus improvement summary.
- State clearly what works, what is still limited and which Mac-only parts were not physically tested.
- Keep everything local and free to use.

**Order:** first inspect reference files and current code, then improve visual renderer and blink/movement behavior, then Mac integration and camera, then tests and README.
