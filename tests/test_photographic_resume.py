import json
import sys

from tools import evaluate_video_photographic_gpt61 as photo


def test_resume_reuses_verified_order_and_preserves_prior_attempts(tmp_path, monkeypatch):
    b, c, m = 'physics_iq_720p24fps', '0001', 'fixture'
    manifest = tmp_path/'manifest.json'
    manifest.write_text(json.dumps({'videos':[{'benchmark':b,'case':c,'model':m,'identity':'fixture-hash',
          'source':'fixture.mp4','frames':120,'fps':24}]}))
    out = tmp_path/'scores'
    target = out/'results'/b/f'{c}_0.json'
    target.parent.mkdir(parents=True)

    def row(trial):
        return {'version':photo.VERSION,'judge':photo.GPT61_JUDGE,'rubric_sha256':photo.RUBRIC_SHA256,
                'benchmark':b,'case':c,'trial':trial,'source_identities':{m:'fixture-hash'},
                'anonymous_mapping':{'A':m},'scores':[{'id':'A','score':3}],'pairs':[]}

    target.write_text(json.dumps(row(0)))
    original = target.read_bytes()
    raw = out/'api_originals'/b/f'{c}_1'
    raw.mkdir(parents=True)
    (raw/'00.json').write_text('old invalid attempt')
    calls = []

    def evaluate(client, benchmark, case, trial, videos, output):
        calls.append(trial)
        progress = json.loads((out/'execution.json').read_text())
        assert progress['completed_calls'] == progress['reused_calls'] == 1
        assert photo.next_raw_index(raw) == 1
        return row(trial)

    monkeypatch.setattr(photo,'OpenAI',lambda **_: object())
    monkeypatch.setattr(photo,'evaluate',evaluate)
    monkeypatch.setenv('PIXELWORLD_JUDGE_API_KEY','fixture-only')
    monkeypatch.setattr(sys,'argv',['photo','--manifest',str(manifest),'--output',str(out),
                                   '--base-url','http://127.0.0.1:1/v1'])
    photo.main()
    assert calls == [1] and target.read_bytes() == original
    assert (raw/'00.json').read_text() == 'old invalid attempt'
    status = json.loads((out/'execution.json').read_text())
    assert status['phase'] == 'finished' and status['completed_calls'] == 2
