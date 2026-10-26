"""Small terminal-cell controls with deterministic layout and hit testing."""
from __future__ import annotations
from dataclasses import dataclass


@dataclass(frozen=True)
class Button:
    mode: str
    row: int   # zero-based row within the control bar
    left: int  # one-based terminal cell, inclusive
    right: int


@dataclass(frozen=True)
class Controls:
    lines: tuple[str,...]
    buttons: tuple[Button,...]

    def hit_test(self,x: int,y: int,*,top_row: int) -> str | None:
        for button in self.buttons:
            if y==top_row+button.row and button.left<=x<=button.right:
                return button.mode
        return None


def build_controls(width: int,active: str,*,colors: bool=True) -> Controls:
    """Wrap whole buttons, including in a narrow tmux split pane."""
    if width<1:
        return Controls((),())
    if width<8:
        return Controls(('m f a'[:width],),())
    entries=(('mouse','Mouse mode'),('camera','Face mode'),('idle','Auto mode'))
    lines=[];buttons=[];pieces=[];used=0;row=0
    for mode,label in entries:
        selected=mode==active
        if width>=14:
            text=f'[>{label}<]' if selected else f'[ {label} ]'
        else:
            short=label.split()[0]
            text=f'<{short}>' if selected else f'[{short}]'
        if used and used+1+len(text)>width:
            lines.append(''.join(pieces)+('\x1b[0m' if colors else ''))
            row+=1;pieces=[];used=0
        if used:
            pieces.append(' ');used+=1
        left=used+1
        color='\x1b[48;2;57;44;25m\x1b[38;2;255;213;136m' if selected else '\x1b[48;2;0;0;0m\x1b[38;2;164;169;171m'
        pieces.append(color+text+'\x1b[0m' if colors else text)
        used+=len(text)
        buttons.append(Button(mode,row,left,used))
    if pieces:
        lines.append(''.join(pieces)+('\x1b[0m' if colors else ''))
    return Controls(tuple(lines),tuple(buttons))
