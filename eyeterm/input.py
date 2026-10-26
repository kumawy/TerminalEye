"""Small bounded parser for local terminal shortcuts and mouse presses.

No keyboard hooks: these bytes arrive only from the foreground terminal. Both
SGR (1006) and legacy X10 coordinates are one-based terminal cells.
"""
from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class InputEvent:
    kind: str
    key: str | None = None
    x: int | None = None
    y: int | None = None
    button: int | None = None


_SGR_PRESS = re.compile(rb'<([0-9]{1,3});([0-9]{1,5});([0-9]{1,5})M')
_KEYS = b'mfaic123qt'
_MAX_SEQUENCE = 128
_PASTE_END = b"\x1b[201~"


class InputParser:
    """Consume fragmented reports without buffering unbounded input."""

    def __init__(self):
        self._state = 'plain'
        self._buffer = bytearray()

    @staticmethod
    def _click(button: int, x: int, y: int) -> InputEvent | None:
        # Motion, wheel, extended buttons and releases are not activations.
        # Shift/Alt/Ctrl modifiers (bits 2..4) do not change the base button.
        if not 0 <= button < 32 or button & 3 == 3 or not (1 <= x <= 65535 and 1 <= y <= 65535):
            return None
        return InputEvent('click', x=x, y=y, button=button & 3)

    def feed(self, data: bytes) -> list[InputEvent]:
        events = []
        for value in data:
            if self._state == "paste":
                # Ignore pasted shell commands completely, including embedded
                # mouse reports and shortcuts; retain only a terminator prefix.
                if value == _PASTE_END[len(self._buffer)]:
                    self._buffer.append(value)
                    if len(self._buffer) == len(_PASTE_END):
                        self._buffer.clear()
                        self._state = "plain"
                else:
                    self._buffer.clear()
                    if value == 27:
                        self._buffer.append(value)
                continue
            # An escape always resynchronizes a partial or malformed report.
            if value == 27:
                self._state = 'escape'
                self._buffer.clear()
                continue
            state = self._state
            if state == 'plain':
                if value in _KEYS:
                    events.append(InputEvent('key', key=chr(value)))
            elif state == 'escape':
                self._state = 'csi' if value == 91 else ('ss3' if value == 79 else 'plain')
            elif state == 'ss3':
                self._state = 'plain'
            elif state == 'csi':
                if not self._buffer and value == 77:  # ESC [ M plus three X10 bytes.
                    self._state = 'x10'
                elif not self._buffer and value == 60:
                    self._buffer.append(value)
                    self._state = 'sgr'
                elif 64 <= value <= 126:
                    self._state = "paste" if self._buffer == b"200" and value == 126 else "plain"
                    self._buffer.clear()
                elif len(self._buffer) < _MAX_SEQUENCE:
                    self._buffer.append(value)
                else:
                    self._buffer.clear()
                    self._state = 'discard_csi'
            elif state == 'x10':
                self._buffer.append(value)
                if len(self._buffer) == 3:
                    button, x, y = (item - 32 for item in self._buffer)
                    if button >= 0:
                        click = self._click(button, x, y)
                        if click:
                            events.append(click)
                    self._buffer.clear()
                    self._state = 'plain'
            elif state == 'sgr':
                self._buffer.append(value)
                if value in (77, 109):
                    match = _SGR_PRESS.fullmatch(self._buffer)
                    if match:
                        click = self._click(*(int(part) for part in match.groups()))
                        if click:
                            events.append(click)
                    self._buffer.clear()
                    self._state = 'plain'
                elif len(self._buffer) >= _MAX_SEQUENCE:
                    self._buffer.clear()
                    self._state = 'discard_sgr'
            elif state == 'discard_sgr':
                if value in (77, 109):
                    self._state = 'plain'
            elif state == 'discard_csi':
                if 64 <= value <= 126:
                    self._state = 'plain'
        return events
