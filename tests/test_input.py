"""Incremental terminal input, including reports split across OS reads."""
import pytest

from eyeterm.input import InputEvent, InputParser


def test_keyboard_and_fragmented_mouse_presses_share_one_stream():
    parser = InputParser()
    assert parser.feed(b'm\x1b[<0;12;') == [InputEvent('key', key='m')]
    assert parser.feed(b'7Mfa123icq') == [
        InputEvent('click', x=12, y=7, button=0),
        *[InputEvent('key', key=key) for key in 'fa123icq'],
    ]


@pytest.mark.parametrize('split', range(1, 13))
def test_every_sgr_report_boundary_preserves_coordinates(split):
    report = b'\x1b[<0;111;23M'
    parser = InputParser()
    assert parser.feed(report[:split]) + parser.feed(report[split:]) == [
        InputEvent('click', x=111, y=23, button=0)
    ]


def test_x10_presses_and_ignored_release_motion_wheel():
    parser = InputParser()
    assert parser.feed(b'\x1b[M' + bytes((32, 42, 37))) == [
        InputEvent('click', x=10, y=5, button=0)
    ]
    ignored = b'\x1b[<0;10;5m\x1b[<32;10;5M\x1b[<64;10;5M'
    ignored += b'\x1b[M' + bytes((35, 42, 37))
    ignored += b'\x1b[M' + bytes((64, 42, 37))
    assert parser.feed(ignored) == []
    assert parser.feed(b'\x1b[<2;4;6M') == [InputEvent('click', x=4, y=6, button=2)]


def test_invalid_coordinates_sequences_and_arrows_cannot_be_shortcuts():
    parser = InputParser()
    reports = b'\x1b[<0;0;2M\x1b[<0;-2;2M\x1b[<0;1;9999999999999M'
    reports += b'\x1b[<watM\x1b[A\x1b[B\x1b[C\x1b[D\x1bOP'
    assert parser.feed(reports) == []
    assert parser.feed(b'q') == [InputEvent('key', key='q')]


def test_malformed_flood_is_bounded_and_recovers_for_next_report():
    parser = InputParser()
    assert parser.feed(b'\x1b[<' + b'1' * 100_000) == []
    assert len(parser._buffer) <= 128
    assert parser.feed(b';2;3M\x1b[<0;4;5Mf') == [
        InputEvent('click', x=4, y=5, button=0), InputEvent('key', key='f')
    ]


def test_escape_interrupts_incomplete_report_and_resynchronizes():
    parser = InputParser()
    assert parser.feed(b'\x1b[<0;4;\x1b[<0;5;6M') == [
        InputEvent('click', x=5, y=6, button=0)
    ]


@pytest.mark.parametrize('split', range(1, 7))
def test_bracketed_paste_cannot_activate_modes_or_mouse_and_can_span_reads(split):
    parser = InputParser()
    begin, end = b'\x1b[200~', b'\x1b[201~'
    assert parser.feed(begin[:split]) == []
    assert parser.feed(begin[split:] + b'camera face mfaic123q\n\x1b[<0;5;6M') == []
    assert parser.feed(end[:split]) + parser.feed(end[split:] + b'a') == [
        InputEvent('key', key='a')
    ]


def test_oversized_bracketed_paste_stays_bounded_and_ignores_escaped_content():
    parser = InputParser()
    assert parser.feed(b'\x1b[200~' + b'f' * 100_000 + b'\x1b\x1b[201xq') == []
    assert len(parser._buffer) <= 128
    assert parser.feed(b'\x1b[201~m') == [InputEvent('key', key='m')]


def test_extended_button_numbers_cannot_become_left_clicks():
    assert InputParser().feed(b'\x1b[<256;5;6M') == []
