"""Generate PCVE edits with the complete local MiniMax-H3 Ref2VA checkpoint."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from pixelworld import pcve
from pixelworld.util import atomic_json,sha256,utc_now


def probe_video(path):
    import av
    with av.open(str(path)) as container:
        stream=container.streams.video[0]
        result={'width':stream.width,'height':stream.height,'fps':float(stream.average_rate),
                'frames':sum(1 for _ in container.decode(stream))}
    if result['fps']!=24 or result['frames']<96:raise ValueError('H3 video must cover all96 PCVE timestamps at24FPS')
    return result


def export_window(raw,target,ffmpeg):
    spec=probe_video(raw);target.parent.mkdir(parents=True,exist_ok=True)
    temporary=target.with_name(target.stem+'.part.mp4')
    subprocess.run([str(ffmpeg),'-nostdin','-y','-v','error','-i',str(raw),'-map','0:v:0',
                    '-frames:v','96','-an','-c:v','libx264rgb','-crf','0','-preset','fast',
                    '-pix_fmt','rgb24',str(temporary)],check=True)
    out=probe_video(temporary)
    if out['frames']!=96:raise ValueError('Evaluation video must contain exactly96frames')
    temporary.replace(target)
    return spec,out


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--bundle',type=Path,required=True)
    p.add_argument('--runtime',type=Path,required=True)
    p.add_argument('--model-path',type=Path,required=True)
    p.add_argument('--ffmpeg',type=Path,required=True)
    p.add_argument('--shard-index',type=int,default=0)
    p.add_argument('--num-shards',type=int,default=1)
    p.add_argument('--num-gpus',type=int,default=4)
    p.add_argument('--limit',type=int)
    p.add_argument('--subset',type=Path)
    args=p.parse_args()
    if not 0<=args.shard_index<args.num_shards:p.error('Invalid shard')
    code=Path(__file__).resolve().parents[1];bundle=args.bundle.resolve();output=bundle/'baselines/minimax_h3_local'
    manifest=code/'data/pcve-rigid/benchmark_manifest.json'
    if sha256(manifest)!=pcve.MANIFEST_SHA256:raise ValueError('Official manifest changed')
    rows=json.loads(manifest.read_text())['edits']
    indices=json.loads(args.subset.read_text())['indices']if args.subset else list(range(len(rows)))
    if len(indices)!=len(set(indices))or any(not isinstance(i,int)or not 0<=i<len(rows)for i in indices):raise ValueError('Invalid subset indices')
    selected=[(i,rows[i])for slot,i in enumerate(indices)if slot%args.num_shards==args.shard_index]
    if args.limit:selected=selected[:args.limit]
    state_path=output/f'shard_{args.shard_index}.json';output.mkdir(parents=True,exist_ok=True)
    state={'shard':args.shard_index,'num_shards':args.num_shards,'pid':os.getpid(),'phase':'loading_model',
           'model':'MiniMaxAI/MiniMax-H3','model_revision':'42ed227ee7df40d41602854ae760620d6eb651fe',
           'started_utc':utc_now(),'case_count':len(selected),'completed':0}
    atomic_json(state_path,state)
    sys.path.insert(0,str(args.runtime.resolve()))
    from fastvideo import VideoGenerator
    from fastvideo.api import (ComponentConfig,EngineConfig,GenerationRequest,GeneratorConfig,InputConfig,
                               OffloadConfig,OutputConfig,ParallelismConfig,PipelineSelection,SamplingConfig)
    from fastvideo.pipelines.basic.minimax_h3 import MiniMaxH3Reference
    generator=VideoGenerator.from_config(GeneratorConfig(model_path=str(args.model_path.resolve()),
        engine=EngineConfig(num_gpus=args.num_gpus,use_fsdp_inference=args.num_gpus>1,
            parallelism=ParallelismConfig(tp_size=1,sp_size=args.num_gpus),
            offload=OffloadConfig(dit=False,dit_layerwise=False,text_encoder=True,vae=True,pin_cpu_memory=False)),
        pipeline=PipelineSelection(workload_type='i2v',
            components=ComponentConfig(override_pipeline_cls_name='MiniMaxH3Ref2VAModularPipeline'))))
    try:
        for index,row in selected:
            case=output/'cases'/f'pcve_{index:04d}';case.mkdir(parents=True,exist_ok=True)
            record_path=case/'generation.json'
            target=output/'predictions/minimax_h3_local/videos'/row['scene']/(row['case_id']+'.mp4')
            with (case/'case.lock').open('a')as lock:
                fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
                prior=json.loads(record_path.read_text())if record_path.exists()else{}
                if prior.get('status')=='completed'and target.is_file()and sha256(target)==prior.get('export_sha256'):
                    state['completed']+=1;continue
                source=code/'data/pcve-rigid'/row['source_video'];prompt=row['prompts']['quantitative']['en']
                raw=case/'raw.mp4';started=time.time()
                record={'case_id':f'pcve_{index:04d}','global_id':row['global_id'],'status':'generating',
                        'source_video_sha256':sha256(source),'prompt':prompt,'prompt_sha256':__import__('hashlib').sha256(prompt.encode()).hexdigest(),
                        'model_revision':state['model_revision'],'seed':42,'sampling_grid_points':50,
                        'requested_frames':124,'fps':24,'width':1344,'height':768,'started_utc':utc_now()}
                atomic_json(record_path,record);state.update(phase='generating',case=row['global_id'],updated_utc=utc_now());atomic_json(state_path,state)
                print('Generating',row['global_id'],flush=True)
                try:
                    reusable=False
                    if raw.is_file():
                        try:probe_video(raw);reusable=True
                        except Exception:raw.rename(raw.with_name('incomplete_'+str(int(time.time()))+'.mp4'))
                    if not reusable:
                        generator.generate(GenerationRequest(prompt=prompt,negative_prompt='',
                            inputs=InputConfig(references=[MiniMaxH3Reference(source=str(source),media_type='video')]),
                            sampling=SamplingConfig(height=768,width=1344,num_frames=124,fps=24,
                                num_inference_steps=50,guidance_scale=1.0,batch_cfg=False,seed=42),
                            output=OutputConfig(output_path=str(raw),save_video=True,return_frames=False)))
                    raw_spec,out_spec=export_window(raw,target,args.ffmpeg)
                    record.update(status='completed',raw_sha256=sha256(raw),export_sha256=sha256(target),
                                  raw_media=raw_spec,evaluated_media=out_spec,elapsed_seconds=time.time()-started,
                                  completed_utc=utc_now(),time_rescaling=False)
                    atomic_json(record_path,record);state['completed']+=1;atomic_json(state_path,state)
                except Exception as e:
                    record.update(status='failed',error=str(e)[-2000:],updated_utc=utc_now());atomic_json(record_path,record);raise
        state.update(phase='finished',updated_utc=utc_now());atomic_json(state_path,state)
    except Exception as e:
        state.update(phase='failed',error=str(e)[-2000:],updated_utc=utc_now());atomic_json(state_path,state);raise
    finally:generator.shutdown()


if __name__=='__main__':main()
