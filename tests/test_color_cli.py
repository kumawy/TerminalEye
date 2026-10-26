"""Color controls and uncluttered wall view through the real CLI loop."""
from io import StringIO
from types import SimpleNamespace

import pytest

from eyeterm import cli
from eyeterm.input import InputEvent, InputParser


def test_theme_catalog_and_new_color_choices_are_available(capsys):
    assert cli.main(['themes']) == 0
    catalog = capsys.readouterr().out
    for name in ('original','matrix','hacker','blue','noir','amber','violet','cyber'):
        assert name in catalog
        assert cli.main(['preview','--theme',name,'--width','20','--height','8']) == 0
        assert '\x1b[38;2;' in capsys.readouterr().out


def test_minimal_view_has_no_hud_and_theme_shortcut_keeps_working(monkeypatch):
    class Output(StringIO):
        def isatty(self): return True
    monkeypatch.setattr(cli.sys,'stdout',Output())
    events=[[],[InputEvent('key',key='t')],[InputEvent('key',key='t')],[InputEvent('key',key='q')]]
    pictures=[]
    themes=[]
    class Screen:
        def __init__(self,**kwargs): assert kwargs['interactive']
        def __enter__(self): return self
        def __exit__(self,*args): return False
        def size(self): return (80,24)
        def read_events(self): return events.pop(0)
        def draw(self,picture,**kwargs): pictures.append(picture)
    def frame(args,*a,**kw):
        themes.append(args.theme)
        return 'eye'
    monkeypatch.setattr(cli,'TerminalSession',Screen)
    monkeypatch.setattr(cli,'frame',frame)
    monkeypatch.setattr(cli.time,'sleep',lambda _:None)
    assert cli.main(['watch','--theme','matrix','--minimal','--seconds','1']) == 0
    assert themes == ['matrix','hacker','blue']
    assert pictures == ['eye','eye','eye']


def test_theme_key_is_local_and_paste_does_not_change_it():
    parser=InputParser()
    assert parser.feed(b't') == [InputEvent('key',key='t')]
    assert parser.feed(b'\x1b[200~tftq\x1b[201~') == []


def test_minimal_view_keeps_errors_and_fallback_visible():
    args=SimpleNamespace(width=None,height=None,style='ascii',cell_aspect=.5,
                         theme='matrix',mono=True,no_controls=False,minimal=True)
    for warning in ('mouse: TRACKING · screen fallback (window unavailable)',
                    'ERROR selecting camera: permission denied'):
        picture,bar,_=cli.watch_view(args,cli.Look(0,0),1,(80,24),'mouse',warning)
        assert warning in picture
        assert not bar.buttons and 'Mouse mode' not in picture


def test_wall_cli_dry_run_outputs_real_plan_without_opening_windows(monkeypatch,capsys):
    import json
    from eyeterm import wall
    monkeypatch.setattr(wall,'read_screens',lambda:[wall.Rect(0,25,1512,880)])
    monkeypatch.setattr(wall.subprocess,'run',lambda *a,**kw:pytest.fail('dry-run opened windows'))
    assert cli.main(['wall','--count','4','--columns','2','--theme','hacker','--dry-run']) == 0
    plan=json.loads(capsys.readouterr().out)
    assert len(plan['windows']) == 4 and plan['columns'] == 2
    assert all(item['theme']=='hacker' for item in plan['windows'])


@pytest.mark.parametrize('minimal',[False,True])
def test_view_only_mouse_still_discloses_window_lookup_fallback(monkeypatch,minimal):
    class Output(StringIO):
        def isatty(self): return True
    class Mouse:
        def __init__(self,*a,**kw): pass
        def target(self): return cli.Look(0,0)
        def status(self): return 'screen fallback (own window unavailable)'
        def close(self): pass
    output=Output()
    monkeypatch.setattr(cli.sys,'stdout',output)
    monkeypatch.setattr('eyeterm.modes.WindowMouseTracker',Mouse)
    arguments=['watch','--mode','mouse','--no-controls','--seconds','.001']
    if minimal: arguments.append('--minimal')
    assert cli.main(arguments) == 0
    assert 'screen fallback' in output.getvalue()


@pytest.mark.parametrize('arguments',[
    ['watch','--mouse-space','invalid'], ['watch','--window-id','0'],
    ['watch','--window-id','-1'], ['watch','--window-id','text'],
])
def test_invalid_window_mouse_options_fail_before_devices_open(arguments):
    with pytest.raises(SystemExit) as error:
        cli.main(arguments)
    assert error.value.code == 2
