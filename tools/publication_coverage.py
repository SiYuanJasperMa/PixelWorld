"""Summarize public upload receipts against the current per-case catalogue."""
from collections import Counter

from tools.prepare_hf_delivery import TRACE_FORMAT_VERSION


def coverage(catalogue, indexes, ledger):
    ready = ledger.get('ready', {})
    groups = []
    for group in catalogue.get('groups', []):
        counts = Counter()
        for history in indexes.get(group['path'], {}).get('cases', {}).values():
            for attempt in history:
                if attempt.get('status') == 'running':
                    counts['running'] += 1
                    continue
                counts['closed_records'] += 1
                receipt = ready.get(attempt['detail'], {})
                digest = attempt.get('detail_sha256')
                current = bool(digest and receipt.get('source_sha256') == digest)
                counts['details_uploaded' if current else 'details_pending'] += 1
                trace = current and receipt.get('trace_ready') and receipt.get('trace_format_version', 0) >= TRACE_FORMAT_VERSION
                counts['traces_uploaded' if trace else 'traces_pending'] += 1
        groups.append({k: group.get(k) for k in ('suite', 'model_key', 'track')} | {
            k: counts[k] for k in ('running', 'closed_records', 'details_uploaded', 'details_pending', 'traces_uploaded', 'traces_pending')})
    totals = {key: sum(r[key] for r in groups) for key in ('running', 'closed_records', 'details_uploaded', 'details_pending', 'traces_uploaded', 'traces_pending')}
    return {'schema': 'pixelworld-publication-coverage-v1', 'updated_utc': catalogue.get('updated_utc'),
            'basis': 'matching detail hashes and completed HF upload receipts; not a full remote byte audit',
            'trace_format_version': TRACE_FORMAT_VERSION, 'totals': totals, 'groups': groups}
