"""Deterministic, supersampled human eye and self-contained truecolor cell encoding.

Coordinates use cell-height units, so iris geometry is independent of terminal
resolution. The supplied reference images are never sampled at runtime.
"""
from __future__ import annotations

from functools import lru_cache
import math
import numpy as np
from PIL import Image

THEME_DESCRIPTIONS = {
    'original': 'Natural skin and green-amber iris',
    'matrix': 'Dark green phosphor on black',
    'hacker': 'Bright terminal green on black',
    'blue': 'Electric blue',
    'noir': 'Black and white',
    'amber': 'Warm amber CRT',
    'violet': 'Ultraviolet',
    'cyber': 'Turquoise and steel blue',
}
THEMES = tuple(THEME_DESCRIPTIONS)
_PHOSPHOR = {
    'matrix': ((.04, .93, .19), 1.0),
    'hacker': ((.025, 1.0, .08), 1.42),
    'blue': ((.16, .46, 1.0), 1.22),
    'amber': ((1.0, .51, .13), 1.2),
    'violet': ((.64, .26, 1.0), 1.24),
}
_SCALE = 3


def _bezier(t, a, b, c, d):
    return (1-t)**3*a + 3*(1-t)**2*t*b + 3*(1-t)*t*t*c + t**3*d


@lru_cache(maxsize=4)
def _surface(width, height, rows_per_cell, aspect, seed):
    """Cache only static geometry/skin; animation never reseeds a frame."""
    h, w = height*rows_per_cell*_SCALE, width*_SCALE
    yy, xx = np.mgrid[:h, :w].astype(np.float32)
    x = ((xx+.5)/_SCALE-width/2)*aspect
    y = (yy+.5)/(_SCALE*rows_per_cell)-height*.51
    half = min(width*aspect*.455, height*1.12)
    nx = x/half
    t = np.clip((nx+1)/2, 0, 1)
    # Linear Bezier x coordinates and asymmetric cubic y coordinates.
    upper = half*_bezier(t, .015, -.47, -.44, -.035)
    lower = half*_bezier(t, .015, .31, .34, -.035)
    seam = half*(.018-.025*t + .024*np.sin(np.pi*t))
    within = np.abs(nx)<1
    rng = np.random.default_rng(seed)
    grain = rng.normal(0, 1, (h,w)).astype(np.float32)
    # Broad orbital/nasal planes plus restrained pores and fine dermal folds.
    orbital = np.exp(-((x/(half*1.15))**2+(y/(half*.7))**2))
    side = np.clip(1-np.abs(nx)*.45, .15, 1)
    plane = 14*orbital + 9*side + 5*np.sin(x/half*2+y/half)
    pores = (1.8*np.sin(x*9.1+y*5.3)*np.sin(y*12.7-x*1.4)
             + grain*1.2 + 1.5*np.sin(x*1.8+y*3.2))
    skin = np.stack((64+plane*1.9+pores*1.5,
                     37+plane*1.2+pores*1.1,
                     31+plane*1.03+pores*.9), axis=-1)
    # Upper crease and orbital shadow stay fixed while mobile lid covers eye.
    crease = upper-half*(.115+.025*np.cos(nx*2))
    for distance, strength, spread in ((crease,-27,half*.022),
                                       (crease-half*.047,13,half*.055),
                                       (lower+half*.115,-16,half*.042),
                                       (lower+half*.165,9,half*.05)):
        skin += (strength*np.exp(-((y-distance)/max(.12,spread))**2)*within)[...,None]*np.array([1,.78,.65])
    # Fine lower folds are curved, low contrast, and static.
    for offset in (.22,.29,.36):
        fold = lower+half*offset + .25*np.sin(nx*5+offset*6)
        skin -= (3*np.exp(-((y-fold)/(half*.012+.08))**2)*within)[...,None]
    vignette = np.clip(1-.32*(x/(width*aspect*.55))**2-.26*(y/(height*.6))**2,.28,1)
    skin *= vignette[...,None]
    return x,y,half,upper,lower,seam,within,skin,grain


def render_eye(width: int, height: int, look_x: float=0.0, look_y: float=0.0,
               openness: float=1.0, *, style: str='ascii', cell_aspect: float=.5,
               theme: str='original', seed: int=5921) -> Image.Image:
    """RGB pixels plus material IDs (0 skin, 1 sclera, 2 iris, 3 pupil, 4 glint).

    ``width``/``height`` are terminal cells, including in half-block mode.
    ``cell_aspect`` is glyph width / line height, normally about 0.5.
    Gaze and openness are clamped; non-finite coordinates are rejected.
    """
    if (not isinstance(width,int) or not isinstance(height,int)
            or not 20<=width<=220 or not 8<=height<=65):
        raise ValueError('width must be 20..220, height 8..65')
    if style not in ('ascii','blocks'):
        raise ValueError('style must be ascii or blocks')
    if theme not in THEMES:
        raise ValueError('theme must be one of: ' + ', '.join(THEMES))
    if not all(math.isfinite(v) for v in (look_x,look_y,openness,cell_aspect)):
        raise ValueError('gaze, openness and cell aspect must be finite')
    if not .25<=cell_aspect<=1:
        raise ValueError('cell aspect must be .25..1.0 (glyph width / line height)')
    look_x,look_y = np.clip([look_x,look_y],-1,1)
    openness = float(np.clip(openness,0,1))
    rpc = 2 if style=='blocks' else 1
    x,y,half,top,bottom,seam,within,base,grain = _surface(width,height,rpc,float(cell_aspect),seed)
    # Most of a blink comes from the upper lid, which physically moves to seam.
    upper = seam+(top-seam)*openness
    lower = seam+(bottom-seam)*openness
    opening = within & (y>upper) & (y<lower)
    if openness==0:
        opening = np.zeros_like(opening)
    skin = base.copy()
    rim_upper = np.exp(-((y-upper)/(half*.016+.08))**2)*within
    rim_lower = np.exp(-((y-lower)/(half*.019+.08))**2)*within
    skin -= (rim_upper*37+rim_lower*19)[...,None]*np.array([1,.8,.69])
    # Warm, raised lower wet line, never an unshaded white outline.
    wet = np.exp(-((y-lower-half*.019)/(half*.014+.07))**2)*within
    skin += (wet*12)[...,None]*np.array([1,.67,.49])
    corners = np.clip(np.abs(x/half),0,1)
    globe_light = .96-.21*corners**2-.08*np.clip(y/half,0,1)
    shadow = (57*np.exp(-np.maximum(y-upper,0)/(half*.075))
              + 23*np.exp(-np.maximum(lower-y,0)/(half*.065)))
    sclera = np.stack((222*globe_light-shadow,
                       222*globe_light-shadow*1.04,
                       204*globe_light-shadow*1.04),axis=-1)
    sclera += (grain*.45+1.3*np.sin(x*2+y*1.2))[...,None]
    # Restrained branching capillaries close to corners, no framewise noise.
    veins = np.exp(-(np.sin(y*3.4+np.sin(x*1.2))/.085)**2)*corners**7
    sclera += veins[...,None]*np.array([3,-4,-4])
    radius = half*.283
    dx = x-(look_x*half*.31+half*.015)
    dy = y-(look_y*half*.115-half*.018)
    r = np.hypot(dx,dy)/radius
    angle = np.arctan2(dy,dx)
    # Multiple angular frequencies and nonlinear radial phase avoid spoke wheels.
    fiber = (np.sin(71*angle+11*r+1.8*np.sin(19*angle))*.45
             +np.sin(127*angle-21*r+2*np.sin(31*angle))*.26
             +np.sin(43*angle+31*r)*.29)
    crypts = np.maximum(0,np.sin(37*angle+9*r))**6
    amber = np.exp(-((r-.49)/.22)**2)
    ring = .5+.5*np.sin(40*r+1.6*np.sin(23*angle))
    iris = np.stack((37+amber*78+fiber*22+ring*8,
                     88+amber*30+fiber*32+ring*9,
                     64-amber*22+fiber*20+ring*6),axis=-1)
    iris -= (crypts*13*np.exp(-((r-.66)/.25)**2))[...,None]
    limbus = .31+.69*np.clip((1-r)/.12,0,1)
    iris *= limbus[...,None]
    # Upper lid casts the same local shadow across sclera and iris.
    iris *= (1-.28*np.exp(-np.maximum(y-upper,0)/(half*.075)))[...,None]
    pupil = r<(.31+.009*np.sin(13*angle))
    iris[pupil] = [5,8,7]
    glint = ((dx+radius*.22)/(radius*.075))**2+((dy+radius*.29)/(radius*.065))**2<1
    iris[glint] = [226,233,213]
    disk = r<1
    sclera[disk] = iris[disk]
    skin[opening] = sclera[opening]
    material = np.zeros(x.shape,dtype=np.uint8)
    material[opening] = 1
    material[opening & disk] = 2
    material[opening & disk & pupil] = 3
    material[opening & disk & glint] = 4
    if theme=='noir':
        luma = skin @ np.array([.2126,.7152,.0722])
        skin = np.repeat(luma[...,None],3,axis=-1)
    elif theme=='cyber':
        skin *= np.where(opening[...,None], np.array([.58,1.06,1.28]),np.array([.48,.66,.89]))
        turquoise = opening & disk & ~pupil
        skin[turquoise] *= np.array([.42,1.25,1.9])
    elif theme in _PHOSPHOR:
        tint, gain = _PHOSPHOR[theme]
        luma = np.clip(skin @ np.array([.2126,.7152,.0722]) / 255, 0, 1)
        # Lift the shadow detail slightly while keeping black pupils and the
        # source's eyelid/iris contrast, then tint the complete eye consistently.
        level = np.power(luma, .9) * (255 * gain)
        skin = level[...,None] * np.array(tint)
    rgb = Image.fromarray(np.uint8(np.clip(skin,0,255)))
    size=(width,height*rpc)
    image=rgb.resize(size,Image.Resampling.LANCZOS)
    image.info['materials']=np.asarray(Image.fromarray(material).resize(size,Image.Resampling.NEAREST))
    image.info['seed']=seed
    return image


def to_ansi(image: Image.Image, style: str='ascii', *, colors: bool=True) -> str:
    """Encode complete independent rows, resetting color/background every row.

    Background is explicitly black so light and dark terminal profiles agree.
    Material-specific glyphs retain the dense punctuation of the video reference.
    No cursor movement or terminal-size assumptions live in the renderer.
    """
    if style not in ('ascii','blocks'):
        raise ValueError('style must be ascii or blocks')
    pixels=np.asarray(image.convert('RGB'))
    h,w,_=pixels.shape
    if style=='blocks' and h%2:
        raise ValueError('blocks image must have an even pixel height')
    material=image.info.get('materials',np.zeros((h,w),dtype=np.uint8))
    yy,xx=np.indices((h,w))
    # Static integer hash: no random generator or dependence on frame/time.
    hash_value=((xx*73+yy*179+(xx*yy*13))%257)/257
    luma=pixels @ np.array([.2126,.7152,.0722])/255
    glyphs=np.empty((h,w),dtype='<U1')
    for mask,palette,brightness in (
        (material==0," .,:;!i1tfj()[]{}",1.8),
        (material==1,"=+*oaO0Q8B@",1.0),
        (material==2,".,:;!il|/txrj1(){}[]xoO",1.6),
        (material==3,"  .",.18),
        (material==4,"*@",1.0)):
        index=np.clip((luma*brightness*.75+hash_value*.25)*(len(palette)-1),0,len(palette)-1).astype(int)
        glyphs[mask]=np.array(list(palette))[index[mask]]
    pieces=[]
    for y in range(0,h,2 if style=='blocks' else 1):
        last_fg=last_bg=None
        if colors and style=='ascii':
            pieces.append('\x1b[48;2;0;0;0m')
        for x in range(w):
            fg=tuple(int(v)//4*4 for v in pixels[y,x])
            if colors and fg!=last_fg:
                pieces.append(f'\x1b[38;2;{fg[0]};{fg[1]};{fg[2]}m')
                last_fg=fg
            if style=='blocks':
                bg=tuple(int(v)//4*4 for v in pixels[y+1,x])
                if colors and bg!=last_bg:
                    pieces.append(f'\x1b[48;2;{bg[0]};{bg[1]};{bg[2]}m')
                    last_bg=bg
                if colors:
                    pieces.append('▀')
                else:
                    # Stable ordered dithering preserves both half-cell samples
                    # even without colors: four combinations of top/bottom ink.
                    top_on = luma[y,x] > .16+.62*hash_value[y,x]
                    bottom_on = luma[y+1,x] > .16+.62*hash_value[y+1,x]
                    pieces.append((' ', '▀', '▄', '█')[int(top_on)+2*int(bottom_on)])
            else:
                pieces.append(str(glyphs[y,x]))
        pieces.append('\x1b[0m\n' if colors else '\n')
    return ''.join(pieces)
