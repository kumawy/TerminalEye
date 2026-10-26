import re
import numpy as np
import pytest
from eyeterm.renderer import render_eye, to_ansi

@pytest.mark.parametrize('kwargs', [
    {'cell_aspect': 0}, {'cell_aspect': float('nan')}, {'look_x': float('inf')},
    {'look_y': float('nan')}, {'openness': float('nan')}, {'theme': 'unknown'},
    {'width': 19}, {'height': 7}, {'width': 1000},
])
def test_reject_invalid_geometry(kwargs):
    args = dict(width=100, height=28)
    args.update(kwargs)
    with pytest.raises(ValueError):
        render_eye(**args)


def test_texture_is_stable_and_seed_changes_skin():
    a = render_eye(100,28,seed=7)
    assert a.tobytes() == render_eye(100,28,seed=7).tobytes()
    assert a.tobytes() != render_eye(100,28,seed=8).tobytes()


def test_gaze_clamps_and_eyelids_stay_fixed():
    center = render_eye(120,36)
    right = render_eye(120,36,look_x=1)
    assert right.tobytes() == render_eye(120,36,look_x=4).tobytes()
    cm, rm = (p.info['materials'] for p in (center,right))
    np.testing.assert_array_equal(cm>0,rm>0)
    assert np.where(rm==3)[1].mean() > np.where(cm==3)[1].mean()
    # Exclude the antialiased boundary: the iris legitimately changes its blend.
    np.testing.assert_array_equal(np.asarray(center)[:5],np.asarray(right)[:5])
    np.testing.assert_array_equal(np.asarray(center)[-5:],np.asarray(right)[-5:])


@pytest.mark.parametrize('aspect', [.35,.5,.75])
def test_iris_is_physically_round(aspect):
    image = render_eye(120,40,style='blocks',cell_aspect=aspect)
    ys,xs = np.where(image.info['materials']>=2)
    physical_width = (xs.max()-xs.min()+1)*aspect
    physical_height = (ys.max()-ys.min()+1)/2
    assert .8 < physical_width/physical_height < 1.25


def test_blink_is_lid_occlusion():
    opened = render_eye(120,36)
    half = render_eye(120,36,openness=.4)
    shut = render_eye(120,36,openness=0)
    assert 0 < np.count_nonzero(half.info['materials']) < np.count_nonzero(opened.info['materials'])
    assert not np.any(shut.info['materials'])
    assert np.asarray(shut).std()>8


@pytest.mark.parametrize('theme', ['original','noir','cyber'])
def test_themes_and_self_contained_ansi_rows(theme):
    pic=render_eye(80,24,theme=theme)
    ansi=to_ansi(pic)
    rows=ansi.splitlines()
    assert len(rows)==24 and all(r.endswith('\x1b[0m') for r in rows)
    assert '\x1b[48;2;0;0;0m' in ansi
    assert all(len(re.sub(r'\x1b\[[0-9;]*m','',r))==80 for r in rows)
    assert '\x1b' not in to_ansi(pic,colors=False)
    if theme=='noir':
        rgb=np.asarray(pic)
        np.testing.assert_array_equal(rgb[...,0],rgb[...,1])
        np.testing.assert_array_equal(rgb[...,1],rgb[...,2])


def test_monochrome_blocks_keep_visible_eye_and_gaze():
    center=to_ansi(render_eye(80,24,style='blocks'),style='blocks',colors=False)
    side=to_ansi(render_eye(80,24,style='blocks',look_x=1),style='blocks',colors=False)
    assert center!=side
    assert len(set(center)-{'\n'})>1
    assert '\x1b' not in center
    assert len(center.splitlines())==24
    assert all(len(row)==80 for row in center.splitlines())


@pytest.mark.parametrize('theme,channel', [('matrix',1),('hacker',1),('blue',2),('amber',0),('violet',2)])
@pytest.mark.parametrize('style', ['ascii','blocks'])
def test_colored_themes_keep_detail_and_geometry_with_requested_hue(theme, channel, style):
    original = render_eye(100,30,style=style)
    image = render_eye(100,30,style=style,theme=theme)
    pixels = np.asarray(image).astype(float)
    means = pixels.mean(axis=(0,1))
    assert means[channel] == means.max()
    assert means[channel] > means[0 if channel != 0 else 2] * 1.5
    if theme == 'violet':
        assert means[0] > means[1] * 1.5
    np.testing.assert_array_equal(image.info['materials'],original.info['materials'])
    assert len(np.unique(pixels.reshape(-1,3),axis=0)) > 50
    material = image.info['materials']
    assert pixels[material==1].mean() > pixels[material==3].mean()*3
    ansi = to_ansi(image,style=style)
    assert len(ansi.splitlines()) == 30


def test_hacker_is_brighter_than_matrix_without_changing_eye_geometry():
    matrix = render_eye(100,30,theme='matrix')
    hacker = render_eye(100,30,theme='hacker')
    np.testing.assert_array_equal(matrix.info['materials'],hacker.info['materials'])
    assert np.asarray(hacker)[...,1].mean() > np.asarray(matrix)[...,1].mean()*1.1
