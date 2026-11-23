import json
import sys

from tools import evaluate_phygen_three_stage_gpt55 as judge


def test_resume_only_schedules_missing_scores(tmp_path, monkeypatch):
    questions = tmp_path/'vendor/phygenbench-official-source/PhyGenBench'
    questions.mkdir(parents=True)
    for name in ('single_question.json','multi_question.json','video_question.json'):
        (questions/name).write_text('[{},{}]')
    output = tmp_path/'out'
    model = 'fixture'
    (output/model).mkdir(parents=True)
    source = tmp_path/'fixture.mp4'
    monkeypatch.setattr(judge,'ROOT',tmp_path)
    monkeypatch.setattr(judge,'model_source',lambda *_: source)
    monkeypatch.setattr(judge,'OpenAI',lambda **_: object())
    monkeypatch.setenv('PIXELWORLD_JUDGE_API_KEY','fixture-only')

    def score(case):
        return {'case':case,'model':model,'source':str(source),'stage_scores':[1,1,1],
                'overall':1.0,'judge':judge.GPT61_JUDGE,'version':judge.GPT61_VERSION}

    existing_path = output/model/'001.json'
    existing_path.write_text(json.dumps(score(1)))
    original = existing_path.read_bytes()
    calls = []

    def evaluate(client, source, questions, output, model, case):
        calls.append(case)
        progress = json.loads((output/'summary.json').read_text())
        assert progress['completed_jobs'] == progress['reused_jobs'] == 1
        return score(case)

    monkeypatch.setattr(judge,'evaluate',evaluate)
    monkeypatch.setattr(sys,'argv',['judge','--bundle',str(tmp_path),'--output',str(output),
            '--models',model,'--cases','2','1','--judge','gpt61-sol',
            '--responses-base-url','http://127.0.0.1:1/v1'])
    judge.main()
    assert calls == [2]
    assert existing_path.read_bytes() == original
    status = json.loads((output/'execution.json').read_text())
    assert status['phase'] == 'finished' and status['completed_jobs'] == 2
