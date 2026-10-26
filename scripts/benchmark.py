#!/usr/bin/env python3
"""Measure local render+ANSI pipeline throughput and paced single-core CPU cost."""
from pathlib import Path
import json
import math
import platform
import sys
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from eyeterm.renderer import render_eye,to_ansi


def measure(width,height,style):
    def frame(n):
        return to_ansi(render_eye(width,height,look_x=.5*math.sin(n*.07),style=style),style)
    frame(0)
    frames=60
    cpu=time.process_time();start=time.perf_counter()
    count=0
    for i in range(frames):count+=len(frame(i).encode('utf-8'))
    elapsed=time.perf_counter()-start;cpu_elapsed=time.process_time()-cpu
    samples=32;target=16
    paced_cpu=time.process_time();paced_start=time.perf_counter()
    for i in range(samples):
        frame(i)
        time.sleep(max(0,paced_start+(i+1)/target-time.perf_counter()))
    paced_elapsed=time.perf_counter()-paced_start
    paced_cpu_elapsed=time.process_time()-paced_cpu
    return dict(width=width,height=height,style=style,frames=frames,
                throughput_fps=round(frames/elapsed,2),cpu_ms_per_frame=round(cpu_elapsed/frames*1000,2),
                bytes_per_full_frame=round(count/frames),paced_fps=round(samples/paced_elapsed,2),
                paced_cpu_percent_one_core=round(paced_cpu_elapsed/paced_elapsed*100,2))


def main():
    data=dict(platform=platform.platform(),python=platform.python_version(),
              scope='render_eye + to_ansi only, warmed static geometry cache; no terminal I/O or camera; CPU relative to one core',
              samples=[measure(w,h,s) for w,h in ((80,24),(100,28)) for s in ('ascii','blocks')])
    out=Path(__file__).resolve().parents[1]/'assets'/'preview'/'benchmark.json'
    out.write_text(json.dumps(data,indent=2)+'\n')
    print(json.dumps(data,indent=2))

if __name__=='__main__':main()
