from eyeterm.renderer import render_eye, to_ansi
from eyeterm.cli import Idle, ease

def test_renderer_sizes_and_modes():
    for style in ('ascii','blocks'):
        pic=render_eye(64,20,openness=.8,look_x=.7,style=style)
        assert pic.size==(64,20*(2 if style=='blocks' else 1))
        ans=to_ansi(pic,style=style)
        assert '\x1b[' in ans
        assert len(ans)>1000

def test_openness_changes_image():
    a=render_eye(80,24,openness=0).tobytes()
    b=render_eye(80,24,openness=1).tobytes()
    assert a!=b

def test_idle_bounds():
    idle=Idle()
    for n in range(1,150):
        pos=idle.sample(idle.last+n*.01)
        assert abs(pos.x)<=1 and abs(pos.y)<=1

def test_ease():
    assert ease(0)==0
    assert ease(1)==1

def test_hook_idempotent_and_reversible(tmp_path,monkeypatch):
    from pathlib import Path
    from eyeterm.cli import install_hook, HOOK_START
    startup=tmp_path/'.zshrc'
    startup.write_text('export FOO=bar\n# keep me\n')
    monkeypatch.setattr(Path,'home', lambda : tmp_path)
    install_hook(True)
    install_hook(True)
    assert startup.read_text().count(HOOK_START)==1
    install_hook(False)
    assert startup.read_text()=='export FOO=bar\n# keep me\n'
    assert len(list(tmp_path.glob('.zshrc.terminal-eye-backup-*')))==1
