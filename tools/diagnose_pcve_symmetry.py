"""Replay saved PCVE tracks to validate symmetric trajectory gating.

Diagnostic outputs are separate from the pinned released-code scores.
No generation or GPU tracking is performed; missing caches fail closed.
"""
import argparse
import json
from pathlib import Path
import statistics
import sys
import types

from pixelworld.util import atomic_json,sha256,utc_now


def symmetric_source(source):
    old='''            p_uv_score, pred_plausible = _presence_gated_uv(
                p_uv, p_area, area_scale)'''
    new=old+'''
            g_uv, _ = _presence_gated_uv(g_uv, g_area, area_scale)
            s_uv_score = None
            if not row.get("added"):
                s_uv_score, _ = _presence_gated_uv(
                    src_tracks[n]["uv"], src_tracks[n]["area"], area_scale)'''
    assert source.count(old)==1
    source=source.replace(old,new)
    old_null='null = traj_error(src_tracks[n]["uv"], g_uv, ref_pres, seed=f,'
    assert source.count(old_null)==1
    return source.replace(old_null,'null = traj_error(s_uv_score, g_uv, ref_pres, seed=f,')


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--bundle',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);args=p.parse_args()
    code=Path(__file__).resolve().parents[1];bundle=args.bundle.resolve();source_root=code/'vendor/pcve-rigid-eval-pinned/eval'
    sys.path.insert(0,str(source_root))
    from metrics import physics
    import numpy as np
    from tools.pcve_native import native_directory,native_cache
    path=source_root/'metrics/physics.py';source=path.read_text()
    module=types.ModuleType('metrics.physics_symmetric_diagnostic');module.__file__=str(path);module.__package__='metrics'
    sys.modules[module.__name__]=module;exec(compile(symmetric_source(source),str(path),'exec'),module.__dict__)
    cache=native_cache(bundle,native_directory(bundle));edits=json.loads((code/'data/pcve-rigid/benchmark_manifest.json').read_text())['edits']
    groups={'source_copy':bundle/'analysis/evaluation_alignment_20261119/pcve_multiarch_source_copy_full129/predictions/control_source_copy',
            'edited_oracle':bundle/'analysis/evaluation_alignment_20261119/pcve_multiarch_edited_oracle_full129/predictions/control_edited_oracle'}
    for model in ['gpt61-sol','gpt56-sol','gpt56-terra']:
        groups[model]=bundle/'evaluation/pcve_standard720p24fps_scheduler'/model/'vision-agent/sam2_native_v2/predictions'/('pixelworld_'+model+'_vision-agent')
    report={'diagnostic_only':True,'proposed_version':'symmetric-trajectory-gating-v1','official_source_sha256':sha256(path),
            'original_source_unchanged':True,'no_generation_or_tracking':True,'updated_utc':utc_now(),'groups':{}}
    for name,folder in groups.items():
        recorded={r['global_id']:r for r in json.loads((folder/'metrics.json').read_text())['per_case']};rows=[]
        for edit in edits:
            tracks_path=folder/'pred_tracks'/(edit['scene']+'__'+edit['case_id']+'.npz')
            with np.load(tracks_path)as z:
                tracks={n:{k:z[n+'__'+k].copy()for k in ['uv','area','mask']}for n in {f.split('__')[0]for f in z.files}}
            results={}
            for kind,cls in [('released_replay',physics.PhysicsScorer),('symmetric',module.PhysicsScorer)]:
                scorer=cls.__new__(cls);scorer.root=code/'data/pcve-rigid';scorer.margin_radii=1.;scorer.min_frames=3
                scorer.cache_dir=cache;scorer.prompts_dir=source_root/'metrics/text_prompts';scorer._gt_cache={};scorer._prompt_cache={}
                def cached_track(video,tag,prompts):
                    f=cache/(tag+'__'+physics._prompt_key(prompts)+'.v2.npz')
                    if not f.is_file():raise FileNotFoundError('Missing frozen GT cache '+str(f))
                    with np.load(f)as z:return {n:{k:z[n+'__'+k].copy()for k in ['uv','area','mask']}for n in prompts}
                scorer._cached_track=cached_track
                scorer.tracker=types.SimpleNamespace(track=lambda *_: {n:{k:v.copy()for k,v in d.items()}for n,d in tracks.items()})
                dummy=np.broadcast_to(np.zeros((1,1,1,3),np.uint8),(96,720,1280,3))
                value=scorer.score(Path('not_read.mp4'),edit,frames=dummy)
                if value.status!='completed'or value.pes is None:raise ValueError((name,edit['global_id'],kind,value.status,value.note))
                results[kind]={'pes':max(value.pes,-1),'traj_px':value.traj_px,'mask_iou':value.mask_iou}
            rows.append({'case':edit['global_id'],'recorded_pes':recorded[edit['global_id']]['pes'],**results})
        report['groups'][name]={'cases':len(rows),'recorded_pes':statistics.fmean(r['recorded_pes']for r in rows),
            'released_replay_pes':statistics.fmean(r['released_replay']['pes']for r in rows),
            'symmetric_pes':statistics.fmean(r['symmetric']['pes']for r in rows),
            'max_replay_delta':max(abs(r['recorded_pes']-r['released_replay']['pes'])for r in rows),'per_case':rows}
        print(name,{k:v for k,v in report['groups'][name].items()if k!='per_case'},flush=True)
        atomic_json(args.output,report)


if __name__=='__main__':main()
