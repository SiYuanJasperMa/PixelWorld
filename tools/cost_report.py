"""Recompute per-model token cost with cached/uncached input separated.

Reasoning tokens are already included in output tokens by the provider, so they
are reported for diagnosis but not charged twice.
"""
from __future__ import annotations
import argparse, collections, json
from pathlib import Path

DEFAULT_PRICES = {
    'gpt61-sol': {'input': 10.0, 'cached_input': 1.0, 'output': 50.0},
    'gpt56-sol': {'input': 10.0, 'cached_input': 1.0, 'output': 50.0},
    'gpt6-astra': {'input': 10.0, 'cached_input': 1.0, 'output': 50.0},
}


def cohort_report(run_root: Path, prices: dict, uniform_price=None):
    """One accepted delivery per case, including audited historical reuse.

    Missing cache counters remain unknown. This is successful-delivery cost;
    failed-attempt overhead must be reported separately.
    """
    result = {}
    for bench in ('physics_iq', 'robojudge', 'pcve', 'phygenbench'):
        selected = {}
        root = run_root / bench
        registry = root / 'reuse_registry.json'
        if registry.is_file():
            for item in json.loads(registry.read_text()).get('entries', []):
                if item['track'] == 'vision-agent':
                    selected[(item['model_key'], item['case_id'])] = Path(item['source']) / 'run_manifest.json'
        def attempt_order(path):
            name, attempt = path.parent.name.rsplit('_attempt_', 1)
            return path.parents[2].name, name, int(attempt)
        for path in sorted(root.glob('*/vision-agent/case_*/run_manifest.json'), key=attempt_order):
            record = json.loads(path.read_text())
            if record.get('status') != 'complete' or not (record.get('validation') or {}).get('pass'):
                continue
            case = record.get('case_id') or path.parent.name.rsplit('_attempt_', 1)[0].removeprefix('case_')
            identity = (record.get('model_key') or path.parents[2].name, case)
            if identity not in selected:
                selected[identity] = path
        grouped = collections.defaultdict(list)
        for (model, case), path in selected.items():
            record = json.loads(path.read_text())
            grouped[model].append(record)
        bench_rows = {}
        for model, records in grouped.items():
            price = uniform_price or prices.get(model)
            usable = []
            for record in records:
                usage = record.get('token_usage') or {}
                a, c, o = (usage.get(k) for k in ('input_tokens', 'cached_input_tokens', 'output_tokens'))
                if all(type(x) is int and x >= 0 for x in (a, c, o)) and c <= a:
                    usable.append((a, c, o))
            a = sum(row[0] for row in usable)
            c = sum(row[1] for row in usable)
            o = sum(row[2] for row in usable)
            entry = {'accepted_cases': len(records), 'cache_usage_complete_cases': len(usable),
                     'cache_usage_missing_cases': len(records) - len(usable),
                     'reported_input_tokens': a, 'reported_cached_input_tokens': c, 'reported_output_tokens': o,
                     'reported_token_weighted_cache_hit_rate': c / a if a else None,
                     'price_assumption': price, 'scope': 'earliest accepted delivery; historical reuse included; retry overhead excluded'}
            pixels=[spec['width']*spec['height']*spec['frames'] for record in records
                    if isinstance((spec:=record.get('media_contract')),dict)
                    and all(type(spec.get(k)) is int and spec[k]>0 for k in ('width','height','frames'))]
            entry['mean_generated_pixels']=sum(pixels)/len(pixels) if pixels else None
            entry['media_measured_cases']=len(pixels)
            if price and usable:
                cost = ((a-c)*price['input'] + c*price['cached_input'] + o*price['output']) / 1e6
                entry['covered_mean_cost_usd'] = cost / len(usable)
                entry['mean_cost_usd'] = cost / len(usable) if len(usable) == len(records) else None
                entry['sensitivity_mean_cost_usd'] = {
                    str(rate): (a*(1-rate)*price['input'] + a*rate*price['cached_input'] + o*price['output']) / 1e6 / len(usable)
                    for rate in (0.7, 0.8)}
            bench_rows[model] = entry
        result[bench] = bench_rows
    return result

def report(run_root: Path, prices: dict, status='complete', assumed_hit_rate=None, uniform_price=None):
    rows = collections.defaultdict(list)
    for bench in ('physics_iq','robojudge','pcve','phygenbench'):
        for p in (run_root/bench).glob('*/vision-agent/case_*/run_manifest.json'):
            try: d = json.loads(p.read_text())
            except (OSError, ValueError): continue
            if d.get('status') != status: continue
            u = d.get('token_usage') or {}
            rows[d.get('model_key')].append({
                'bench': bench, 'case': p.parent.name,
                'input': u.get('input_tokens'), 'cached': u.get('cached_input_tokens') or 0,
                'output': u.get('output_tokens'), 'reasoning': u.get('reasoning_output_tokens'),
                'duration': d.get('duration_seconds'),
            })
    out = {}
    for model, items in sorted(rows.items()):
        price = uniform_price or prices.get(model)
        inp = sum(x['input'] or 0 for x in items); cached = sum(x['cached'] or 0 for x in items)
        out_tok = sum(x['output'] or 0 for x in items); reason = sum(x['reasoning'] or 0 for x in items)
        effective_cached = inp * assumed_hit_rate if assumed_hit_rate is not None else cached
        entry = {'cases': len(items), 'input_tokens': inp, 'cached_input_tokens': cached,
                 'uncached_input_tokens': inp-cached, 'output_tokens': out_tok,
                 'reasoning_output_tokens': reason,
                 'cache_hit_rate': cached/inp if inp else None,
                 'mean_input_tokens': inp/len(items) if items else None,
                 'mean_output_tokens': out_tok/len(items) if items else None,
                 'mean_duration_minutes': (sum(x['duration'] or 0 for x in items)/len(items)/60) if items else None,
                 'effective_cached_input_tokens': effective_cached,
                 'effective_uncached_input_tokens': inp-effective_cached,
                 'effective_cache_hit_rate': (effective_cached/inp) if inp else None,
                 'price_assumption': price}
        if price:
            measured_cost = (inp-cached)/1e6*price['input'] + cached/1e6*price['cached_input'] + out_tok/1e6*price['output']
            estimated_cost = (inp-effective_cached)/1e6*price['input'] + effective_cached/1e6*price['cached_input'] + out_tok/1e6*price['output']
            entry['measured_cost_usd'] = round(measured_cost, 4)
            entry['measured_mean_cost_per_case_usd'] = round(measured_cost/len(items), 4)
            entry['estimated_cost_usd'] = round(estimated_cost, 4)
            entry['estimated_mean_cost_per_case_usd'] = round(estimated_cost/len(items), 4)
            no_cache = inp/1e6*price['input'] + out_tok/1e6*price['output']
            entry['no_cache_cost_usd'] = round(no_cache, 4)
            entry['estimated_cache_saving_fraction'] = round(1-estimated_cost/no_cache, 4) if no_cache else None
        out[model] = entry
    return out

def main():
    p=argparse.ArgumentParser();p.add_argument('--run-root',type=Path,required=True)
    p.add_argument('--prices',type=Path);p.add_argument('--status',default='complete')
    p.add_argument('--cache-hit-rate',type=float,help='Unified assumed cache hit rate for estimation')
    p.add_argument('--uniform-prices',action='store_true',help='Apply the GPT-6.1 price schedule to every model')
    p.add_argument('--scope',choices=['attempts','accepted-cohorts'],default='attempts',
                   help='Accepted cohorts deduplicate deliveries and include audited historical reuse')
    p.add_argument('--output',type=Path)
    a=p.parse_args();prices=json.loads(a.prices.read_text()) if a.prices else DEFAULT_PRICES
    uniform=prices.get('gpt61-sol') if a.uniform_prices else None
    result=(cohort_report(a.run_root.resolve(),prices,uniform) if a.scope=='accepted-cohorts'
            else report(a.run_root.resolve(),prices,a.status,a.cache_hit_rate,uniform))
    text=json.dumps(result,indent=2)
    if a.output:a.output.write_text(text+'\n')
    print(text)

if __name__=='__main__':main()
