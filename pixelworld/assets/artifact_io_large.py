"""Output-only large RGB storage helpers. No generation or checking policy."""
from pathlib import Path
import json
import numpy as np
from PIL import Image, ImageDraw


def save(frames,case_dir,audio=None):
    case_dir=Path(case_dir);spec=json.loads((case_dir/'task_spec.json').read_text())
    n,h,w,fps=(spec[k]for k in ('frames','height','width','fps'))
    assert len(frames)==n,len(frames)
    assert spec.get('audio') is None and audio is None
    # A 120-frame 4K uint8 video is about 3 GiB and its packed uint32 tensor is
    # another 3.7 GiB. Keep the packed tensor on disk while constructing it so
    # artifact serialization does not double each run's resident memory.
    packing=case_dir/'pixels.packing.npy'
    packed=np.lib.format.open_memmap(packing,mode='w+',dtype=np.uint32,shape=(h,w,n))
    folder=case_dir/'frames';folder.mkdir(exist_ok=True)
    temporary=case_dir/'pixels.npz.partial'
    try:
        for index,frame in enumerate(frames):
            frame=np.asarray(frame)
            assert frame.shape==(h,w,3) and frame.dtype==np.uint8,(index,frame.shape,frame.dtype)
            value=frame[...,0].astype(np.uint32);value<<=16
            value|=frame[...,1].astype(np.uint32)<<8;value|=frame[...,2]
            packed[:,:,index]=value
            Image.fromarray(frame).save(folder/f'{index:02d}.png')
        packed.flush()
        with temporary.open('wb') as stream:np.savez_compressed(stream,pixels=packed)
        temporary.replace(case_dir/'pixels.npz')
    finally:
        del packed
        temporary.unlink(missing_ok=True);packing.unlink(missing_ok=True)
    tw=min(w,512);th=round(h*tw/w)
    def sheet(indices,name):
        image=Image.new('RGB',(tw*4,(th+21)*2),(25,28,35));draw=ImageDraw.Draw(image)
        for slot,index in enumerate(indices):
            x,y=(slot%4)*tw,(slot//4)*(th+21)
            draw.text((x+5,y+3),f'frame {index:02d} · {index/fps:.2f}s',fill='white')
            image.paste(Image.fromarray(np.asarray(frames[index])).resize((tw,th),Image.Resampling.LANCZOS),(x,y+21))
        image.save(case_dir/name)
    sheet(np.linspace(0,n-1,8).round().astype(int),'inspection.png')
    sheet([min(i,n-1)for i in range(4)]*2,'transition_check.png')
    # Local file playback fetches one PNG at a time rather than embedding a
    # multi-gigabyte video in HTML. Saved pixels and PNGs remain native 4K.
    payload=json.dumps({'width':w,'height':h,'frames':n,'fps':fps}).replace('<','\\u003c')
    (case_dir/'index.html').write_text('''<!doctype html><meta charset="utf-8"><title>RGB video</title>
<style>body{background:#10141c;color:white;font:16px system-ui}canvas{max-width:100%;height:auto}button,input{font:inherit}</style>
<canvas id="screen"></canvas><p><button id="play">播放 / 暂停</button> <button id="replay">重播</button>
<input id="seek" type="range" min="0" value="0"><span id="time"></span></p><script>
const spec='''+payload+''';const canvas=document.querySelector('#screen');canvas.width=spec.width;canvas.height=spec.height;
const ctx=canvas.getContext('2d'),seek=document.querySelector('#seek');seek.max=spec.frames-1;
let frame=0,playing=false,timer;async function show(index){frame=index;const image=new Image();
image.src='frames/'+String(index).padStart(2,'0')+'.png';await image.decode();ctx.drawImage(image,0,0);
seek.value=index;document.querySelector('#time').textContent=(index/spec.fps).toFixed(2)+' / '+(spec.frames/spec.fps).toFixed(2)+' s';}
async function tick(){if(!playing)return;await show(frame);if(frame+1<spec.frames){frame++;timer=setTimeout(tick,1000/spec.fps)}else playing=false;}
document.querySelector('#play').onclick=()=>{playing=!playing;if(playing)tick();else clearTimeout(timer)};
document.querySelector('#replay').onclick=()=>{clearTimeout(timer);frame=0;playing=true;tick()};
seek.oninput=()=>{playing=false;clearTimeout(timer);show(+seek.value)};show(0);</script>''')
