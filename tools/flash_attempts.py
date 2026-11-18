"""Select case outcomes without discarding any Flash attempt history."""
import json

from pixelworld.retries import infrastructure_failure


def accepted(record):
    return (record.get('status') == 'complete'
            and record.get('validation', {}).get('pass')
            and not infrastructure_failure(record))


def histories(folder):
    cases = {}
    for path in sorted(folder.glob('case_*_attempt_*/run_manifest.json')):
        record = json.loads(path.read_text())
        case = record.get('case_id') or path.parent.name.split('_attempt_')[0][5:]
        cases.setdefault(case, []).append((path, record))
    return {case: sorted(rows, key=lambda row: row[1].get('attempt', int(row[0].parent.name.rsplit('_', 1)[1])))
            for case, rows in cases.items()}


def selected(rows):
    """Prefer the first accepted delivery; otherwise surface running/model/infra."""
    success = next((row for row in rows if accepted(row[1])), None)
    if success:
        return success
    running = [row for row in rows if row[1].get('status') == 'running']
    if running:
        return running[-1]
    model = [row for row in rows if not infrastructure_failure(row[1])]
    return (model or rows)[-1]


def outcome(rows):
    _, record = selected(rows)
    if infrastructure_failure(record):
        return 'stale'
    return record.get('status') or 'unknown'
