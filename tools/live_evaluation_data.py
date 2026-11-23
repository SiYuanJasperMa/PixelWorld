"""Export sanitized full-cohort scores and evaluation stages for live Pages."""
import json
import math
import time
from pathlib import Path
from datetime import datetime, timezone

from pixelworld.constants import PHYSICS_IQ_COMMIT, PHYSICS_IQ_DATASET_REVISION
from pixelworld import pcve
from pixelworld.smoke_web import clean
from tools.evaluate_robojudge_shared50 import CODE_REVISION, CHECKPOINT_REVISION, DATASET_REVISION, TASKS_SHA256
from tools.phygen_protocol import FRAME_SAMPLING_REVISION,MAIN_JUDGE_FOLDER,TEXT_JUDGE_FOLDER
from tools.phygen_protocol import THREE_STAGE_JUDGE_FOLDER,THREE_STAGE_REVISION
from tools.pcve_native import native_summary,NATIVE_EVALUATION

COHORTS = {
    'physics_iq_720p24fps':('Physics-IQ Verified · 720p24fps','physics_720p24fps_full_scheduler',198),
    'physics_iq_4k24fps':('Physics-IQ Verified · 4K24fps','physics_4k24fps_full_scheduler',198),
    'physics_iq':('Physics-IQ Verified','physics_full_scheduler',198),
    'physics_iq_gpt_native_v2':('Physics-IQ Verified','physics_gpt_native_v2_full_scheduler',198),
    'physics_iq_seed21_native_v2':('Physics-IQ Verified','physics_seed21_native_v2_full_scheduler',198),
    'physics_iq_512w24fps_gpt_native':('Physics-IQ Verified · 512w24fps','physics_highres_512w24fps_full_scheduler',198),
    'robojudge_main720p24fps':('RoboJudge shared-50 · 720p24fps','robojudge_main720p24fps_scheduler',50),
    'pcve_standard720p24fps':('PCVE-RigidBench · official 720p24fps','pcve_standard720p24fps_scheduler',129),
    'phygenbench_t2v_main720p24fps':('PhyGenBench T2V · 720p24fps',MAIN_JUDGE_FOLDER,160),
    'robojudge':('RoboJudge shared-50','robojudge_full_baseline100k',50),
    'robojudge_seed21_native_v2':('RoboJudge shared-50','robojudge_seed21_native_v2_full',50),
    'pcve_smoke':('PCVE-RigidBench','pcve_full_scheduler',129),
    'pcve_text':('PCVE-RigidBench · Text','pcve_text_scheduler',129),
    'robojudge_text':('RoboJudge shared-50 · Text','robojudge_text_scheduler',50),
    'phygenbench_text':('PhyGenBench · Text',TEXT_JUDGE_FOLDER,160),
    'physics_iq_harness_pi':('Physics-IQ Verified · Pi1.6.0','physics_iq_harness_pi_scheduler',198),
    **{key: ('Physics-IQ Verified · '+label, key+'_scheduler',198) for key,label in (
        ('physics_iq_text','Text'), ('physics_iq_res480','480p'), ('physics_iq_res1080','1080p'),
        ('physics_iq_fps12','12 FPS'), ('physics_iq_fps48','48 FPS'),
        ('physics_iq_thinking_low','lower effort'), ('physics_iq_thinking_higher','higher effort'))},
}


def read(path):
    try:return json.loads(path.read_text())
    except (OSError,ValueError):return {}


def finite(value):
    return not isinstance(value,bool) and isinstance(value,(int,float)) and math.isfinite(value)


def note(value):
    if isinstance(value,dict):
        value='; '.join(str(value[k]) for k in ('phase','blocker','missing_relative_path','missing','detail','reason') if value.get(k))
    return clean(str(value or ''))[:1200]


def live_commands():
    values=[]
    if not Path('/proc').is_dir():return values
    for proc in Path('/proc').iterdir():
        if not proc.name.isdigit():continue
        try:
            if (proc/'stat').read_text().rsplit(') ',1)[1].split()[0]=='Z':continue
            argv=[v.decode(errors='replace') for v in (proc/'cmdline').read_bytes().split(b'\0') if v]
            values.append((int(proc.name),argv))
        except OSError:continue
    return values


def evaluator_active(cohort, commands):
    path=str(cohort.resolve())
    for _,argv in commands:
        identity=('physiq.run_physics_iq' in argv or 'tools.evaluate_pcve_full' in argv
                  or 'tools.evaluate_robojudge_shared50' in argv or
                  any(Path(v).name in ('evaluate_pcve_full.py','evaluate_robojudge_shared50.py') for v in argv))
        if identity and any(v.startswith(path) for v in argv):return True
        if argv and Path(argv[0]).name=='ssh' and any(path in v and 'official_driver.py' in v for v in argv):return True
    return False


def physics_score(score, model, track, require_isolated_cache=False):
    if require_isolated_cache and score.get('generated_mask_cache_revision') != 'cohort-isolated-v1':
        return None
    if not (score.get('inputs')==198 and score.get('upstream_commit')==PHYSICS_IQ_COMMIT
            and score.get('dataset_revision')==PHYSICS_IQ_DATASET_REVISION
            and score.get('model_key')==model and score.get('track')==track
            and finite(score.get('verified_score')) and finite(score.get('original_formula_score'))):
        return None
    return {'metrics':{'Verified':100*score['verified_score'],'Original formula':100*score['original_formula_score']},
            'units':'/100','scope':198,'prompt_setting':score.get('prompt_setting'),
            'independent_runs':score.get('independent_runs',1),
            'fps':score.get('fps'),'frames':score.get('frames'),'resolution':score.get('resolution'),
            'completed_utc':score.get('completed_utc'),'source_revision':score['upstream_commit'],
            'dataset_revision':score['dataset_revision'],'metrics_sha256':score.get('official_metrics_sha256')}


def robo_score(summary, plan, execution, model, track):
    rows=summary.get('items') or []
    expected={f'common50_{i:04d}' for i in range(50)}
    if not (summary.get('complete_shared50') is True and summary.get('valid_paired_count')==50
            and plan.get('scope',{}).get('complete_shared50') is True
            and plan.get('scope',{}).get('prepared_count')==50
            and not any(plan.get('scope',{}).get(k) for k in ('missing','failed','duplicates'))
            and plan.get('model')==model and plan.get('track')==track
            and plan.get('code_revision')==CODE_REVISION and plan.get('checkpoint_revision')==CHECKPOINT_REVISION
            and plan.get('dataset_revision')==DATASET_REVISION and plan.get('tasks_sha256')==TASKS_SHA256
            and execution.get('returncode')==0 and len(rows)==50
            and {r.get('case_id') for r in rows}==expected):
        return None
    if any(not finite(r.get(k)) or not 1<=r[k]<=5 for r in rows for k in ('PA','IA')):
        return None
    means={k:sum(r[k] for r in rows)/50 for k in ('PA','IA')}
    means['mean']=(means['PA']+means['IA'])/2
    reported=summary.get('shared50_means') or {}
    if any(not finite(reported.get(k)) or abs(reported[k]-v)>1e-9 for k,v in means.items()):
        return None
    return {'metrics':{'PA':means['PA'],'IA':means['IA'],'Mean':means['mean']},'units':'1–5','scope':50,
            'source_revision':CODE_REVISION,'checkpoint_revision':CHECKPOINT_REVISION,
            'dataset_revision':DATASET_REVISION,'table8_comparability_verified':False,
            'case_scores':{r['case_id']:{'PA':r['PA'],'IA':r['IA'],'Mean':(r['PA']+r['IA'])/2} for r in rows}}


def pcve_score(summary, model, track):
    scores=summary.get('scores') or {};overall=scores.get('overall') or {}
    fields={'PES':'pes','TE px':'traj_px','Mask IoU':'mask_iou','PSNR':'psnr','SSIM':'ssim','LPIPS':'lpips','CLIP':'clip_sim'}
    if not (summary.get('complete_official129') is True and summary.get('case_count')==129
            and summary.get('source_revision')==pcve.EVALUATOR_REVISION
            and summary.get('dataset_revision')==pcve.DATASET_REVISION
            and summary.get('model')==model and summary.get('track')==track
            and all(finite(overall.get(k)) for k in fields.values()) and finite(scores.get('fvd'))):
        return None
    return {'metrics':{**{name:overall[key] for name,key in fields.items()},'FVD':scores['fvd']},
            'units':'original metrics','scope':129,'fps':summary.get('fps'),
            'frames':summary.get('frames'),'resolution':summary.get('resolution'),
            'completed_utc':summary.get('completed_utc'),'source_revision':summary['source_revision'],
            'dataset_revision':summary['dataset_revision'],'metrics_sha256':summary.get('official_metrics_sha256'),
            'sam2_native_postprocessing':summary.get('sam2_native_postprocessing') is True,
            'sam2_native_extension_sha256':summary.get('sam2_native_extension_sha256'),
            'validated_numeric_physics_cases':summary.get('validated_numeric_physics_cases'),
            'published_table1_comparability_verified':summary.get('published_table1_comparability_verified') is True,
            'evaluation_label':summary.get('evaluation_label','Pinned released evaluator')}


def phygen_score(summary, model, track):
    if summary.get('frame_sampling_revision')!=FRAME_SAMPLING_REVISION:return None
    if summary.get('track', 'vision-agent') != track:
        return None
    entry=(summary.get('models') or {}).get(model)
    if not entry or entry.get('cases')!=160:
        return None
    mean=entry.get('mean_0_1');mean15=entry.get('mean_1_5')
    if not finite(mean) or not finite(mean15):
        return None
    metrics={'Overall':mean,'Mean_1_5':mean15}
    for name,value in (entry.get('dimensions') or {}).items():
        if finite(value.get('mean_0_1')):
            metrics[name]=value['mean_0_1']
    return {'metrics':metrics,'units':'0-1 (GPT-5.5 judge)','scope':160,
            'judge_model':summary.get('judge_model'),
            'judge_protocol':summary.get('judge_protocol'),
            'frame_sampling_revision':summary.get('frame_sampling_revision'),
            'official_gpt4o_phygeneval':False}


def phygen_three_stage_score(summary,model,track):
    if summary.get('version')!=THREE_STAGE_REVISION or summary.get('full160')is not True:return None
    row=summary.get('models',{}).get(model,{})
    if row.get('cases')!=160 or not finite(row.get('mean')):return None
    return {'metrics':{'Overall':row['mean']},'scope':160,'units':'0-1 (GPT-5.5 three-stage MCQ)',
            'judge_model':'gpt-5.5-2026-04-24','judge_protocol':THREE_STAGE_REVISION,
            'frame_sampling_revision':'chronological-shared-rgb','official_gpt4o_phygeneval':False}


def collect(run_root: Path):
    result={'updated_utc':datetime.now(timezone.utc).isoformat(),'cohorts':[]}
    references=read(run_root/'evaluation/pcve_paper_reference_table.json')
    if not references:references=read(Path(__file__).resolve().parents[1]/'docs/PCVE_TABLE1_REFERENCE.json')
    if references:result['benchmark_references']={'pcve':references}
    commands=live_commands()
    worker_health=read(run_root/'state/evaluator_worker_health.json')
    age=time.time()-worker_health.get('checked_unix',0)
    if 0<=age<180:
        commands.extend((pid,argv) for pid,argv in worker_health.get('commands',[]))
    gpu_health=read(run_root/'state/gpu_evaluator_worker_health.json')
    if 0<=time.time()-gpu_health.get('checked_unix',0)<180:
        commands.extend((pid,argv) for pid,argv in gpu_health.get('commands',[]))
    registry=read(run_root/'live_sources.json')
    sources={item['key']:Path(item['runs_root']) for item in registry.get('sources',[])}
    recovery=read(run_root/'state/main_evaluation_recovery.json')
    for suite,(benchmark,folder,expected) in COHORTS.items():
        generation=read(sources.get(suite,run_root/'generations'/suite)/'status.json')
        scheduler=read(run_root/'evaluation'/folder/'scheduler.json')
        entries=scheduler.get('cohorts') or scheduler.get('done') or {}
        # PhyGen judge schedulers key cohorts by model and declare one track.
        # Other schedulers already use model/track keys.
        entries={key if '/' in key else key+'/'+scheduler.get('track','vision-agent'):value
                 for key,value in entries.items()}
        groups=generation.get('groups') or {}
        for key in sorted(set(groups)|set(entries)|set(scheduler.get('blocked') or {})):
            model,track=key.split('/',1)
            if model=='gemini38-flash':continue
            group=groups.get(key) or {};counts=group.get('counts') or {};entry=entries.get(key) or {}
            cohort=run_root/'evaluation'/folder/model/track
            if suite=='robojudge_main720p24fps' and model=='deepseek-v41':
                scope=read(cohort/'plan.json').get('scope') or {}
                if scope.get('complete_shared50') is True and scope.get('prepared_count')==50:
                    counts={'complete':50,'running':0,'pending':0,'exhausted':0}
            current_pcve=suite in ('pcve_standard720p24fps','pcve_text')
            evaluation_cohort=native_summary(cohort).parent if current_pcve else cohort
            execution=read(evaluation_cohort/'execution.json')
            active=evaluator_active(cohort,commands)
            blocked=(scheduler.get('blocked') or {}).get(key) or {}
            phase=blocked.get('phase') or entry.get('phase') or 'waiting_for_generation'
            blocker=blocked or entry.get('blocker') or entry.get('error')
            if active and execution.get('phase')=='official_evaluation' and phase in ('adapter_evaluation','failed'):
                phase='official_evaluation'
            if phase=='failed':phase='waiting_for_evaluation_retry'
            row={'suite':suite,'benchmark':benchmark,'model_key':model,'track':track,
                 'expected':expected,'accepted':counts.get('complete',0),'generation_counts':counts,
                 'phase':phase,'blocker':note(blocker),'scheduler_updated_utc':scheduler.get('updated_utc'),
                 'full_scope_scored':False,'score':None,'evaluator_active':active}
            if current_pcve:
                row['evaluation_progress']={'completed':execution.get('evaluated_cases',0),'expected':129,
                                            'updated_utc':execution.get('updated_utc')}
            if suite.startswith('physics_iq'):
                current = suite == 'physics_iq_720p24fps' or suite in {
                    'physics_iq_text','physics_iq_res480','physics_iq_res1080',
                    'physics_iq_fps12','physics_iq_fps48','physics_iq_thinking_low','physics_iq_thinking_higher',
                    'physics_iq_harness_pi'}
                score=physics_score(read(cohort/'scores.json'),model,track,require_isolated_cache=current)
            elif suite.startswith('robojudge'):
                score=robo_score(read(cohort/'summary.json'),read(cohort/'plan.json'),execution,model,track)
            elif suite.startswith('pcve'):
                score=pcve_score(read(evaluation_cohort/'summary.json'),model,track)
                if current_pcve and score and not score['sam2_native_postprocessing']:
                    score=None
                if current_pcve:
                    row['evaluation_revision']=NATIVE_EVALUATION
                    row['previous_environment_score']=pcve_score(read(cohort/'summary.json'),model,track)
            elif suite.startswith('phygenbench'):
                judge_folder=TEXT_JUDGE_FOLDER if suite=='phygenbench_text' else MAIN_JUDGE_FOLDER
                if suite in ('phygenbench_t2v_main720p24fps', 'phygenbench_text'):
                    judge_folder=('phygenbench_text_gpt55_three_stage_mcq_v1'
                                  if suite=='phygenbench_text' else THREE_STAGE_JUDGE_FOLDER)
                    summary=read(run_root/'evaluation'/judge_folder/'summary.json')
                    score=phygen_three_stage_score(summary,model,track)
                    done=summary.get('models',{}).get(model,{}).get('cases')
                    if (summary.get('version')==THREE_STAGE_REVISION
                            and isinstance(done,int) and not isinstance(done,bool)
                            and 0<=done<=row['accepted']):
                        row['evaluation_progress']={'completed':done,'expected':expected}
                else:score=phygen_score(read(run_root/'evaluation'/judge_folder/'summary.json'),model,track)
            else:
                score=None
            if score and row['accepted']==expected:
                row.update(score=score,full_scope_scored=True,phase='complete',blocker='')
            elif score:
                row.update(phase='blocked_current_generation_mismatch',blocker='Scored scope disagrees with current validated generation inventory')
            elif phase=='complete' or entry.get('phase')=='full_shared50_scored':
                row.update(phase='blocked_score_provenance',blocker='Full score metadata or original task identities did not pass verification')
            elif active and row['accepted']==expected:
                row.update(phase='official_evaluation',blocker='')
            elif phase in ('official_evaluation','official_full_evaluation','adapter_evaluation') and not active:
                watcher_alive=any(pid==scheduler.get('pid') and any(Path(v).name.startswith('watch_') for v in argv)
                                  for pid,argv in commands)
                row.update(phase='preparing_evaluation' if watcher_alive else 'stale_evaluation')
                if not watcher_alive:row['blocker']='Recorded evaluator stage has no matching live process'
            elif row['accepted']==expected and phase=='waiting_for_generation':
                row['phase']='waiting_for_evaluator'
            elif not counts.get('running',0) and not counts.get('pending',0) and row['accepted']<expected and counts.get('exhausted',0):
                row.update(phase='incomplete_generation',blocker='Full official scope is incomplete; no missing-video score imputation')
            if suite.startswith('pcve') and row['accepted']==expected and not row['full_scope_scored']:
                waiting=read(cohort/'worker_state.json')
                gpu=read(cohort/'gpu_worker.json')
                worker_live=any(any(Path(v).name=='pcve_worker_start.py' or v=='tools.run_pcve_gpu_worker' for v in argv)
                    and model in argv for _,argv in commands)
                if worker_live and waiting.get('phase')=='waiting_for_verified_checkpoint':
                    row.update(phase='preparing_evaluation',blocker='GPU worker ready; verifying shared evaluator checkpoint')
                elif worker_live and gpu.get('phase')=='waiting_for_free_gpu':
                    row.update(phase='preparing_evaluation',blocker='Waiting for a GPU with sufficient free memory')
                elif worker_live and waiting.get('sam2_native_postprocessing'):
                    row.update(phase='official_evaluation',blocker='Verified SAM2 native postprocessing; full 129 cases, eight metrics')
                elif current_pcve and row.get('previous_environment_score') and not active:
                    row.update(phase='waiting_for_evaluator',blocker='Previous score retained; re-evaluation uses verified SAM2 native postprocessing')
            if suite.startswith('phygenbench') and row['accepted']==expected and not row['full_scope_scored']:
                judge_folder=TEXT_JUDGE_FOLDER if suite=='phygenbench_text' else MAIN_JUDGE_FOLDER
                judge=read(run_root/'evaluation'/judge_folder/'scheduler.json')
                judge_live=any(pid==judge.get('judge_pid') and 'tools.evaluate_phygen_gpt55' in argv
                               for pid,argv in commands)
                if model in judge.get('current_models',[]) and judge_live:
                    row.update(phase='adapter_evaluation',evaluator_active=True,
                               blocker='PixelWorld GPT-5.5 judge; adapted scoring, not official PhyGenEval')
                if suite in ('phygenbench_t2v_main720p24fps', 'phygenbench_text'):
                    folder=run_root/'evaluation'/('phygenbench_text_gpt55_three_stage_mcq_v1'
                        if suite=='phygenbench_text' else THREE_STAGE_JUDGE_FOLDER)
                    progress=read(folder/'summary.json');done=progress.get('models',{}).get(model,{}).get('cases',0)
                    done=max(done,len(list((folder/model).glob('[0-9][0-9][0-9].json'))))
                    alive=any(('tools.evaluate_phygen_three_stage_gpt55'in a and str(folder)in a)
                              or any(Path(v).name=='watch_three_stage_scores.py' for v in a)
                              for _,a in commands)
                    row.update(phase='adapter_evaluation'if alive else'waiting_for_evaluator',evaluator_active=alive,
                               evaluation_progress={'completed':done,'expected':160},
                               blocker='GPT-5.5 three-stage MCQ; prior single-score results are legacy diagnostics')
            if (suite=='physics_iq_720p24fps' and row['accepted']==expected
                    and not row['full_scope_scored'] and not active
                    and recovery.get('phase')=='waiting_for_reference_and_previous_export'):
                row.update(phase='preparing_evaluation',
                           blocker='Preparing pinned reference resources and preserving the current video export')
            result['cohorts'].append(row)
    return result
