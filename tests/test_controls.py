import re
import pytest
from eyeterm import controls

SGR=re.compile(r'\x1b\[[0-9;]*m')


def test_exact_labels_and_hit_regions_follow_wrapped_rows():
    bar=controls.build_controls(32,'idle')
    assert len(bar.lines)==2
    plain=''.join(SGR.sub('',line) for line in bar.lines)
    for label in ('Mouse mode','Face mode','Auto mode'):assert label in plain
    assert [b.mode for b in bar.buttons]==['mouse','camera','idle']
    for b in bar.buttons:
        assert bar.hit_test(b.left,b.row+10,top_row=10)==b.mode
        assert bar.hit_test(b.right,b.row+10,top_row=10)==b.mode
    assert bar.hit_test(1,9,top_row=10) is None
    assert bar.hit_test(32,12,top_row=10) is None


def test_selected_button_changes_style_without_moving_targets():
    idle=controls.build_controls(79,'idle')
    camera=controls.build_controls(79,'camera')
    assert len(idle.lines)==1
    assert idle.lines!=camera.lines
    assert idle.buttons==camera.buttons
    assert '>Face mode<' in SGR.sub('',camera.lines[0])


@pytest.mark.parametrize('width',[0,1,6,13,14,20,27,32,79])
def test_controls_never_overflow_and_targets_stay_within_visible_cells(width):
    bar=controls.build_controls(width,'mouse')
    assert all(len(SGR.sub('',line))<=width for line in bar.lines)
    assert all(1<=b.left<=b.right<=width for b in bar.buttons)
    assert all(0<=b.row<len(bar.lines) for b in bar.buttons)


def test_monochrome_controls_keep_selection_without_any_color_sequences():
    bar=controls.build_controls(79,'camera',colors=False)
    assert '>Face mode<' in bar.lines[0]
    assert '\x1b' not in ''.join(bar.lines)
