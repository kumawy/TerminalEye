"""Native wall planning and launch boundaries without opening terminal apps."""
from __future__ import annotations

import argparse
import io
import json
import os
from pathlib import Path
import plistlib
import shlex
import subprocess
import sys

import pytest

from eyeterm import wall


def parse(*options):
    parser = argparse.ArgumentParser()
    wall.add_parser(parser.add_subparsers(dest='cmd', required=True))
    return parser.parse_args(['wall', *options])


def test_visible_frame_converts_cocoa_points_to_quartz_with_negative_monitor_origins():
    assert wall.quartz_visible_frame((-1280, 40, 1280, 984), 1080) == wall.Rect(-1280, 56, 1280, 984)
    assert wall.quartz_visible_frame((0, 1080, 1920, 1056), 1080) == wall.Rect(0, -1056, 1920, 1056)


def test_nine_windows_fill_visible_frame_in_a_nonoverlapping_three_by_three_grid():
    cells = wall.layout_windows(wall.Rect(0, 25, 1512, 880))
    assert len(cells) == 9
    assert cells[0].bounds == (12, 37, 500, 314)
    assert cells[1].bounds == (512, 37, 1000, 314)
    assert cells[3].bounds == (12, 326, 500, 603)
    assert cells[-1].bounds == (1012, 615, 1500, 892)
    for index, first in enumerate(cells):
        for second in cells[index + 1:]:
            assert (first.right <= second.x or second.right <= first.x or
                    first.bottom <= second.y or second.bottom <= first.y)


def test_layout_inward_rounds_fractional_screen_points_and_respects_manual_columns():
    cells = wall.layout_windows(wall.Rect(-1000.5, 30.3, 1000.8, 800.2), count=4, columns=2,
                                gap=10, margin=12)
    assert cells[0].bounds == (-988, 43, -505, 425)
    assert cells[-1].right <= -12
    assert cells[-1].bottom <= 818


@pytest.mark.parametrize('options', [
    ['--count', '0'], ['--count', '37'], ['--count', '1.2'], ['--columns', '0'],
    ['--gap', '81'], ['--margin', '-1'], ['--screen', '0'], ['--fps', '31'],
    ['--fps', 'nan'], ['--mode', 'camera'], ['--app', 'xterm'],
])
def test_parser_rejects_invalid_wall_options(options):
    with pytest.raises(SystemExit):
        parse(*options)


def test_invalid_grid_fails_before_reading_screen_or_creating_files(monkeypatch):
    monkeypatch.setattr(wall, 'read_screens', lambda: pytest.fail('screen read before validation'))
    monkeypatch.setattr(wall.tempfile, 'mkdtemp', lambda **kwargs: pytest.fail('temporary files created'))
    with pytest.raises(ValueError, match='columns'):
        wall.run_wall(parse('--count', '2', '--columns', '3'))


def test_undersized_grid_and_missing_display_fail_before_native_launch(monkeypatch):
    monkeypatch.setattr(wall.tempfile, 'mkdtemp', lambda **kwargs: pytest.fail('temporary files created'))
    with pytest.raises(ValueError, match='small|fit'):
        wall.run_wall(parse('--count', '36'), screens=[wall.Rect(0, 25, 1280, 750)])
    with pytest.raises(ValueError, match='screen'):
        wall.run_wall(parse('--screen', '2'), screens=[wall.Rect(0, 25, 1512, 880)])


def test_dry_run_prints_bounds_and_window_relative_commands_without_native_launch(monkeypatch):
    monkeypatch.setattr(wall.tempfile, 'mkdtemp', lambda **kwargs: pytest.fail('temporary files created'))
    output = io.StringIO()
    result = wall.run_wall(parse('--dry-run', '--theme', 'mixed'),
                           screens=[wall.Rect(0, 25, 1512, 880)], out=output,
                           runner=lambda *args, **kwargs: pytest.fail('native app launched'))
    plan = json.loads(output.getvalue())
    assert result == 0
    assert len(plan['windows']) == 9
    assert plan['columns'] == plan['rows'] == 3
    assert plan['windows'][0]['bounds'] == [12, 37, 500, 314]
    assert plan['windows'][0]['theme'] == 'original'
    assert 'matrix' in {item['theme'] for item in plan['windows']}
    assert plan['windows'][0]['theme'] == plan['windows'][8]['theme']
    command = shlex.split(plan['windows'][0]['command'])
    assert command[1:5] == ['-m', 'eyeterm', 'watch', '--mode']
    assert command[5] == 'mouse'
    assert command[command.index('--mouse-space') + 1] == 'window'
    assert '--window-id' in command and '--minimal' in command
    assert '--fps' in command and command[command.index('--fps') + 1] == '12'


def test_bootstrap_quotes_hostile_unicode_paths_and_sets_guard_before_any_shell_startup(tmp_path):
    directory = tmp_path / "eye's $cash `echo compromised` Қара"
    directory.mkdir()
    python = directory / 'python'
    log = directory / 'argv.json'
    python.write_text(f'#!{sys.executable}\nimport os,sys,json\n'
                      f'open({str(log)!r},"w").write(json.dumps({{"argv":sys.argv[1:],'
                      '"disable":os.environ.get("TERMINAL_EYE_DISABLE"),'
                      '"path":os.environ.get("PYTHONPATH")}))\n')
    python.chmod(0o755)
    spec = directory / "child's $log `value`.json"
    command = wall.bootstrap_command(spec, interpreter=str(python), project=str(directory))
    result = subprocess.run(['/bin/zsh', '-f', '-c', command], capture_output=True, timeout=5)
    assert result.returncode == 0, result.stderr.decode()
    assert result.stdout == b''
    assert json.loads(log.read_text()) == {
        'argv': ['-m', 'eyeterm.wall', '--child', str(spec)], 'disable': '1', 'path': str(directory)}
    assert not (tmp_path / 'compromised').exists()


@pytest.mark.parametrize('app', ['terminal', 'iterm'])
def test_launch_passes_data_as_argv_and_returns_ids_to_only_owned_watch_children(tmp_path, monkeypatch, app):
    temp = tmp_path / "one's $wall `fake` Қара"
    temp.mkdir()
    monkeypatch.setattr(wall.tempfile, 'mkdtemp', lambda **kwargs: str(temp))
    observed = []
    native = []

    def runner(argv, **kwargs):
        assert argv[0:4] == ['/usr/bin/osascript', '-l', 'JavaScript', '-']
        assert kwargs['check'] is False and kwargs['timeout'] >= 5
        if app == 'terminal':
            profile = plistlib.loads(Path(argv[4]).read_bytes())
            assert profile['RunCommandAsShell'] is False
            command = profile['CommandString']
            assert profile['name'] == 'Terminal Eye Wall'
            assert 'Terminal Eye' in profile['CustomTitle']
        else:
            command = argv[4]
        bootstrap = shlex.split(command)
        assert bootstrap[:3] == ['/usr/bin/env', 'TERMINAL_EYE_DISABLE=1', 'TERMINAL_EYE_ACTIVE=1']
        spec = Path(bootstrap[-1])
        payload = json.loads(spec.read_text())
        observed.append((spec, payload))
        left, top, right, bottom = map(int, argv[-4:])
        native.append(native_record(1700 + len(observed), (left, top, right-left, bottom-top),
                                    'Terminal' if app == 'terminal' else 'iTerm2'))
        identity = {'id': 700 + len(observed), 'bounds': {'x': left, 'y': top,
                    'width': right-left, 'height': bottom-top}}
        return subprocess.CompletedProcess(argv, 0, stdout=json.dumps(identity), stderr='')

    output = io.StringIO()
    assert wall.run_wall(parse('--count', '2', '--app', app, '--mode', 'idle', '--theme', 'noir'),
                         screens=[wall.Rect(0, 25, 1512, 880)], runner=runner, native_reader=lambda: native, out=output) == 0
    assert len(observed) == 2
    for index, (spec, payload) in enumerate(observed):
        assert int(spec.with_suffix('.id').read_text()) == 1701 + index
        assert payload['argv'][payload['argv'].index('--mode') + 1] == 'idle'
        assert payload['argv'][payload['argv'].index('--theme') + 1] == 'noir'
    assert '1701' in output.getvalue() and '1702' in output.getvalue()


def test_child_execs_without_shell_and_cleans_only_its_own_launch_files(tmp_path, monkeypatch):
    folder = tmp_path / 'run'
    folder.mkdir()
    spec = folder / '0.json'
    profile = spec.with_suffix('.terminal')
    profile.write_text('owned')
    unrelated = folder / 'other.json'
    unrelated.write_text('other window')
    spec.write_text(json.dumps({'argv': ['/safe/python', '-m', 'eyeterm', 'watch', '--mode', 'mouse']}))
    spec.with_suffix('.id').write_text('987')
    calls = []
    monkeypatch.setattr(wall.os, 'execve', lambda executable, argv, env: calls.append((executable, argv, env)))
    assert wall.run_child(spec) == 0
    executable, argv, environment = calls[0]
    assert executable == '/safe/python'
    assert argv == ['/safe/python', '-m', 'eyeterm', 'watch', '--mode', 'mouse', '--window-id', '987']
    assert environment['TERMINAL_EYE_DISABLE'] == '1'
    assert unrelated.read_text() == 'other window'
    assert not spec.exists() and not profile.exists() and not spec.with_suffix('.id').exists()


def test_automation_denial_is_clear_and_never_retries_or_mutates_other_windows(tmp_path, monkeypatch):
    temp = tmp_path / 'owned'
    temp.mkdir()
    monkeypatch.setattr(wall.tempfile, 'mkdtemp', lambda **kwargs: str(temp))
    calls = []

    def deny(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 1, stdout='', stderr='Not authorized to send Apple events. (-1743)')

    with pytest.raises(RuntimeError, match='Automation.*denied'):
        wall.run_wall(parse('--count', '2'), screens=[wall.Rect(0, 25, 1512, 880)], runner=deny)
    assert len(calls) == 1


def test_first_child_cannot_remove_run_directory_before_later_window_specs_exist(tmp_path, monkeypatch):
    directory = tmp_path / 'owned'
    directory.mkdir()
    monkeypatch.setattr(wall.tempfile, 'mkdtemp', lambda **kwargs: str(directory))
    monkeypatch.setattr(wall.os, 'execve', lambda *args: None)
    original_replace = Path.replace

    def replace_and_start_child(source, target):
        result = original_replace(source, target)
        # The child can receive its ID immediately, before the next loop starts.
        wall.run_child(Path(target).with_suffix('.json'))
        return result

    monkeypatch.setattr(Path, 'replace', replace_and_start_child)

    native = []

    def runner(argv, **kwargs):
        left, top, right, bottom = map(int, argv[-4:])
        identifier = 700 + len(native)
        native.append(native_record(identifier, (left, top, right-left, bottom-top)))
        identity = {'id': identifier, 'bounds': {'x': left, 'y': top,
                    'width': right-left, 'height': bottom-top}}
        return subprocess.CompletedProcess(argv, 0, stdout=json.dumps(identity), stderr='')

    assert wall.run_wall(parse('--count', '3'), screens=[wall.Rect(0, 25, 1512, 880)],
                         runner=runner, native_reader=lambda: native, out=io.StringIO()) == 0
    assert not directory.exists()


def test_screen_numbers_have_no_artificial_upper_bound_but_still_validate_before_launch():
    with pytest.raises(ValueError, match='screen 1000 is unavailable'):
        wall.run_wall(parse('--screen', '1000'), screens=[wall.Rect(0, 25, 1512, 880)])


def native_record(identifier, bounds, owner='Terminal'):
    return {'kCGWindowNumber': identifier, 'kCGWindowOwnerName': owner, 'kCGWindowLayer': 0,
            'kCGWindowBounds': {'X': bounds[0], 'Y': bounds[1], 'Width': bounds[2], 'Height': bounds[3]}}


def test_window_identity_maps_only_own_app_and_exact_bounds_to_native_cg_id():
    rect = wall.Rect(-900, 37, 488, 277)
    records = [native_record(10, (-900, 37, 488, 277), 'Other App'),
               native_record(42, (-800, 37, 488, 277)),
               native_record(901, (-900, 37, 488, 277))]
    assert wall.canonical_window_id(42, rect, 'terminal', records) == '901'
    with pytest.raises(RuntimeError, match='native window'):
        wall.canonical_window_id(42, rect, 'iterm', records)


def test_window_identity_prefers_verified_id_but_rejects_ambiguous_fallback_geometry():
    rect = wall.Rect(12, 37, 488, 277)
    records = [native_record(901, (12, 37, 488, 277)), native_record(902, (12, 37, 488, 277))]
    assert wall.canonical_window_id(901, rect, 'terminal', records) == '901'
    with pytest.raises(RuntimeError, match='ambiguous'):
        wall.canonical_window_id(42, rect, 'terminal', records)


def test_jxa_fits_font_grid_rounding_inside_cell_without_moving_or_mutating_existing_windows():
    if not Path('/usr/bin/osascript').is_file():
        pytest.skip('macOS JXA runtime unavailable')
    script = wall._FIT_WINDOW + '''
function run() {
    var actual, attempts = 0;
    var win = {id: function() { return 42; }};
    Object.defineProperty(win, 'bounds', {
        get: function() { return function() { return actual; }; },
        set: function(request) {
            attempts++;
            actual = {x: request.x, y: request.y,
                width: Math.ceil(request.width / 7) * 7,
                height: Math.ceil(request.height / 14) * 14};
        }
    });
    var answer = JSON.parse(fitWindow(win, [12, 37, 500, 314]));
    answer.attempts = attempts;
    return JSON.stringify(answer);
}
'''
    result = subprocess.run(['/usr/bin/osascript', '-l', 'JavaScript', '-'], input=script,
                            text=True, capture_output=True, timeout=5)
    assert result.returncode == 0, result.stderr
    fitted = json.loads(result.stdout)
    assert fitted == {'id': 42, 'bounds': {'x': 12, 'y': 37, 'width': 483, 'height': 266}, 'attempts': 2}


def test_native_identity_waits_briefly_for_windowserver_metadata_to_settle(monkeypatch):
    rect = wall.Rect(12, 37, 488, 277)
    snapshots = iter([[], [native_record(901, (12, 37, 488, 277))]])
    monkeypatch.setattr(wall.time, 'sleep', lambda delay: None)
    assert wall.await_native_id(42, rect, 'terminal', lambda: next(snapshots)) == '901'


def test_child_timeout_cleans_only_its_own_data_and_preserves_parent_sentinel(tmp_path, monkeypatch):
    spec = tmp_path / '0.json'
    spec.write_text(json.dumps({'argv': ['/never/execute']}))
    profile = spec.with_suffix('.terminal')
    profile.write_text('owned profile')
    sentinel = tmp_path / '.launching'
    sentinel.touch()
    unrelated = tmp_path / '1.json'
    unrelated.write_text('another child')
    moments = iter([0, 21])
    monkeypatch.setattr(wall.time, 'monotonic', lambda: next(moments))
    monkeypatch.setattr(wall.os, 'execve', lambda *args: pytest.fail('timed-out child executed watch'))
    with pytest.raises(RuntimeError, match='did not provide'):
        wall.run_child(spec)
    assert not spec.exists() and not profile.exists()
    assert sentinel.exists() and unrelated.read_text() == 'another child'
