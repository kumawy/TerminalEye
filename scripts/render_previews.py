#!/usr/bin/env python3
"""Save actual ANSI frames and rasterize their cells (not source RGB images).

Run from project root: python scripts/render_previews.py
A terminal screenshot still depends on its font and line spacing. These PNGs
use the emitted foreground/background/glyphs in Menlo or a local monospace font.
"""
from pathlib import Path
import re
import sys
from PIL import Image, ImageDraw, ImageFont
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from eyeterm.renderer import THEMES, render_eye, to_ansi

OUT=Path(__file__).resolve().parents[1]/'assets'/'preview'
SGR=re.compile(r'\x1b\[([0-9;]*)m')


def rasterize_ansi(source, path, cell_width=8, cell_height=16):
    candidates=('/System/Library/Fonts/Menlo.ttc',
                '/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf')
    font=next((ImageFont.truetype(p,13) for p in candidates if Path(p).exists()),ImageFont.load_default())
    rows=source.splitlines()
    cols=max(len(SGR.sub('',row)) for row in rows)
    image=Image.new('RGB',(cols*cell_width,len(rows)*cell_height),'black')
    draw=ImageDraw.Draw(image)
    for y,row in enumerate(rows):
        fg,bg=(220,220,220),(0,0,0)
        x=0; pos=0
        while pos<len(row):
            match=SGR.match(row,pos)
            if match:
                codes=[int(v) for v in match[1].split(';') if v]
                if codes==[0] or not codes:
                    fg,bg=(220,220,220),(0,0,0)
                elif len(codes)==5 and codes[1]==2:
                    if codes[0]==38: fg=tuple(codes[2:])
                    if codes[0]==48: bg=tuple(codes[2:])
                pos=match.end();continue
            ch=row[pos]
            left,top=x*cell_width,y*cell_height
            if ch=='▀':
                draw.rectangle((left,top,left+cell_width-1,top+cell_height//2-1),fill=fg)
                draw.rectangle((left,top+cell_height//2,left+cell_width-1,top+cell_height-1),fill=bg)
            else:
                draw.rectangle((left,top,left+cell_width-1,top+cell_height-1),fill=bg)
                draw.text((left,top),ch,font=font,fill=fg,stroke_width=0)
            pos+=1;x+=1
    image.save(path)
    return image


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    for style in ('ascii','blocks'):
        for prefix in ('before','after'):
            file=OUT/f'{prefix}_{style}.ansi'
            if prefix=='after':
                file.write_text(to_ansi(render_eye(120,36,style=style),style),encoding='utf-8')
            if file.exists(): rasterize_ansi(file.read_text(encoding='utf-8'),OUT/f'{prefix}_{style}.png')
    palette_cells=[]
    for theme in THEMES:
        ansi=to_ansi(render_eye(120,36,theme=theme))
        (OUT/f'{theme}_ascii.ansi').write_text(ansi,encoding='utf-8')
        preview=rasterize_ansi(ansi,OUT/f'{theme}_ascii.png')
        tile=Image.new('RGB',(preview.width,preview.height+32),'#050505')
        tile.paste(preview,(0,32))
        ImageDraw.Draw(tile).text((12,9),theme,fill='white')
        palette_cells.append(tile)
    palette=Image.new('RGB',(palette_cells[0].width*2,palette_cells[0].height*4),'black')
    for i,tile in enumerate(palette_cells):
        palette.paste(tile,((i%2)*tile.width,(i//2)*tile.height))
    palette.save(OUT/'themes.png')
    cells=[]
    for openness,look in ((0,0),(.35,0),(.7,0),(1,0),(1,-.8),(1,.8)):
        ansi=to_ansi(render_eye(100,30,look_x=look,openness=openness))
        path=OUT/f'state_{len(cells)}.png'
        cells.append(rasterize_ansi(ansi,path))
    contact=Image.new('RGB',(cells[0].width*2,cells[0].height*3),'black')
    for i,cell in enumerate(cells):contact.paste(cell,((i%2)*cell.width,(i//2)*cell.height))
    contact.save(OUT/'sequence.png')

if __name__=='__main__':main()
