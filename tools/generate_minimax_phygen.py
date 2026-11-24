"""Generate official PhyGenBench captions with the pinned full H3 T2V model."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from pixelworld.util import atomic_json, sha256, utc_now
from tools.generate_minimax_pcve import probe_video


MODEL_REVISION = '42ed227ee7df40d41602854ae760620d6eb651fe'
RUNTIME_REVISION = 'd995516da00c24105aa841df1e690d3cd8a6c173'


def captions(code):
    root = code/'vendor/phygenbench-official-source/PhyGenBench'
    rows = json.loads((root/'prompts.json').read_text())
    questions = json.loads((root/'video_question.json').read_text())
    if len(rows) != 160 or [r['caption'] for r in rows] != [r['caption'] for r in questions]:
        raise ValueError('Official160 captions must match evaluator question order')
    return rows


def selected_cases(rows, cases=None, limit=None):
    indices=cases if cases is not None else list(range(1,len(rows)+1))
    if len(indices)!=len(set(indices))or any(type(i)is not int or not 1<=i<=len(rows)for i in indices):
        raise ValueError('Case IDs must be distinct official prompt indices')
    selected=[(i,rows[i-1])for i in indices]
    return selected[:limit]if limit else selected


def export_window(raw, target, ffmpeg):
    raw_spec = probe_video(raw)
    if raw_spec['frames'] < 120:
        raise ValueError('Need all120 evaluation timestamps')
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.stem+'.part.mp4')
    subprocess.run([str(ffmpeg),'-nostdin','-y','-v','error','-i',str(raw),
        '-map','0:v:0','-frames:v','120','-an','-c:v','libx264rgb','-crf','0',
        '-preset','fast','-pix_fmt','rgb24',str(temporary)],check=True)
    spec = probe_video(temporary)
    if spec['frames'] != 120:
        raise ValueError('Evaluation export must be exactly120 frames at24FPS')
    temporary.replace(target)
    return raw_spec, spec


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle',type=Path,required=True)
    parser.add_argument('--runtime',type=Path,required=True)
    parser.add_argument('--model-path',type=Path,required=True)
    parser.add_argument('--ffmpeg',type=Path,required=True)
    parser.add_argument('--num-gpus',type=int,default=2)
    parser.add_argument('--limit',type=int)
    parser.add_argument('--cases',type=int,nargs='+',help='Explicit disjoint batch of official prompt IDs')
    parser.add_argument('--output-root',type=Path,help='Private batch directory within the cloud bundle')
    args = parser.parse_args()
    code = Path(__file__).resolve().parents[1]
    base = args.output_root.absolute() if args.output_root else args.bundle.resolve()/'baselines/minimax_h3_phygen'
    if not base.is_relative_to(args.bundle.resolve()):
        raise ValueError('Batch output must remain inside the cloud bundle')
    base.mkdir(parents=True,exist_ok=True)
    guard = (base/'generation.lock').open('a')
    fcntl.flock(guard,fcntl.LOCK_EX|fcntl.LOCK_NB)
    runtime_revision = subprocess.check_output(['git','-C',str(args.runtime),'rev-parse','HEAD'],text=True).strip()
    if runtime_revision != RUNTIME_REVISION or subprocess.run(['git','-C',str(args.runtime),'diff','--quiet','HEAD']).returncode:
        raise ValueError('Pinned unchanged H3 runtime required')
    rows = captions(code)
    selected = selected_cases(rows,args.cases,args.limit)
    state = {'phase':'loading_model','total':len(selected),'completed':0,'failed_cases':0,
        'pid':os.getpid(),'model':'MiniMaxAI/MiniMax-H3','model_revision':MODEL_REVISION,
        'runtime_revision':runtime_revision,'task_type':'t2v','fps':24,'native_canvas':[1344,768],
        'raw_requested_frames':124,'evaluation_frames':120,'sampling_grid_points':50,'seed':42,
        'updated_utc':utc_now()}
    for case,_ in selected:
        f = base/'cases'/f'phygen_{case:03d}'/'generation.json'
        if f.exists() and json.loads(f.read_text()).get('status')=='completed':
            state['completed'] += 1
    atomic_json(base/'progress.json',state)
    sys.path.insert(0,str(args.runtime.resolve()))
    from fastvideo import VideoGenerator
    from fastvideo.api import (ComponentConfig,EngineConfig,GenerationRequest,GeneratorConfig,
        OffloadConfig,OutputConfig,ParallelismConfig,PipelineSelection,SamplingConfig)
    generator = VideoGenerator.from_config(GeneratorConfig(model_path=str(args.model_path.resolve()),
        pipeline=PipelineSelection(workload_type='t2v',
            components=ComponentConfig(override_pipeline_cls_name='MiniMaxH3ModularPipeline')),
        engine=EngineConfig(num_gpus=args.num_gpus,use_fsdp_inference=args.num_gpus>1,
            parallelism=ParallelismConfig(tp_size=1,sp_size=args.num_gpus),
            offload=OffloadConfig(dit=False,dit_layerwise=False,text_encoder=True,vae=True,pin_cpu_memory=False))))
    try:
        state['completed'] = 0
        for case,row in selected:
            directory = base/'cases'/f'phygen_{case:03d}'
            directory.mkdir(parents=True,exist_ok=True)
            record_file = directory/'generation.json'
            video = base/'videos'/f'phygen_{case:03d}.mp4'
            previous = json.loads(record_file.read_text()) if record_file.exists() else {}
            if previous.get('status')=='completed' and video.is_file() and sha256(video)==previous.get('export_sha256'):
                state['completed'] += 1
                continue
            prompt = row['caption']
            record = {'case':case,'case_id':f'phygen_{case:03d}','status':'generating',
                'prompt':prompt,'prompt_sha256':hashlib.sha256(prompt.encode()).hexdigest(),
                'model_revision':MODEL_REVISION,'task_type':'t2v','seed':42,'started_utc':utc_now()}
            atomic_json(record_file,record)
            state.update(phase='smoke_generating' if state['completed']==0 else 'generation_running',
                case=case,updated_utc=utc_now())
            atomic_json(base/'progress.json',state)
            started=time.time();raw=directory/'raw.mp4'
            try:
                reusable = False
                if raw.is_file():
                    try: reusable = probe_video(raw)['frames'] >= 120
                    except Exception: pass
                if not reusable:
                    generator.generate(GenerationRequest(prompt=prompt,negative_prompt='',
                        sampling=SamplingConfig(height=768,width=1344,num_frames=124,fps=24,
                            num_inference_steps=50,guidance_scale=1.0,batch_cfg=False,seed=42),
                        output=OutputConfig(output_path=str(raw),save_video=True,return_frames=False)))
                raw_spec,spec = export_window(raw,video,args.ffmpeg)
                record.update(status='completed',raw_media=raw_spec,evaluated_media=spec,
                    raw_sha256=sha256(raw),export_sha256=sha256(video),elapsed_seconds=time.time()-started,
                    completed_utc=utc_now(),time_rescaling=False)
                atomic_json(record_file,record)
                state['completed'] += 1
                state.update(phase='generation_running',smoke_passed=True,updated_utc=utc_now())
                atomic_json(base/'progress.json',state)
                print('DELIVERED',case,record['elapsed_seconds'],flush=True)
            except Exception as error:
                record.update(status='failed',error=str(error)[-2000:],updated_utc=utc_now())
                atomic_json(record_file,record)
                raise
        state.update(phase='generated' if not args.limit else 'smoke_complete',updated_utc=utc_now())
        atomic_json(base/'progress.json',state)
    except Exception as error:
        state.update(phase='generation_failed',error=str(error)[-2000:],failed_cases=1,updated_utc=utc_now())
        atomic_json(base/'progress.json',state)
        raise
    finally:
        generator.shutdown()


if __name__=='__main__':
    main()
