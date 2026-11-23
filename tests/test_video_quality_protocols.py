import json

import numpy as np
import pytest

from tools.evaluate_physics_iq_fvd import frechet, matched_references
from tools.evaluate_video_photographic_gpt61 import checked_result, load_manifest, summarize


def test_photographic_score_is_anchored_and_requires_each_pair_once():
    decoded = {'scores':[{'id':'A','score':5},{'id':'B','score':None}],
               'pairs':[{'left':'A','right':'B','winner':'tie'}]}
    assert checked_result(json.dumps(decoded), 'AB') == decoded
    decoded['pairs'].append(decoded['pairs'][0])
    with pytest.raises(ValueError,match='duplicate pair'):
        checked_result(json.dumps(decoded), 'AB')
    decoded['pairs'].pop()
    decoded['scores'][0]['score'] = True
    with pytest.raises(ValueError,match='Invalid anchored score'):
        checked_result(json.dumps(decoded), 'AB')


def test_quality_summary_does_not_impute_missing_evidence():
    result = summarize([{'benchmark':'rb','case':'0001','anonymous_mapping':{'A':'m'},
                         'scores':[{'id':'A','score':None}],'pairs':[]}])
    assert result['cohorts'] == [{'benchmark':'rb','model':'m','cases':1,'ratings':0,'no_evidence':1,'mean':None}]


def test_quality_manifest_refuses_duplicate_identities(tmp_path):
    m = tmp_path/'manifest.json'
    row = {'benchmark':'physics','case':'0001','model':'m','frames':120,'fps':24}
    m.write_text(json.dumps({'videos':[row,row]}))
    with pytest.raises(ValueError,match='duplicate cohort'):
        load_manifest(m)


def test_frechet_equal_covariances_is_squared_mean_distance():
    a = np.array([[1.,2.],[0.,1.],[-1.,3.]])
    assert frechet(a,a) == pytest.approx(0,abs=1e-12)
    b = a + [2.,3.]
    assert frechet(a,b) == pytest.approx(13,abs=1e-10)
    assert frechet(b,a) == pytest.approx(13,abs=1e-10)


def test_fvd_selects_only_exact_official_take1_identity(tmp_path):
    ref = tmp_path/'0001_testing-videos_24FPS_left_take-1_fall.mp4'
    ref.touch()
    (tmp_path/'0001_testing-videos_24FPS_left_take-2_fall.mp4').touch()
    cases = [{'id':'0001','generated_video_name':'0001_left_fall.mp4'}]
    assert matched_references(tmp_path,cases) == {'0001':ref}
    with pytest.raises(ValueError, match='reference pool'):
        matched_references(tmp_path,[{'id':'0001','generated_video_name':'0001_right_fall.mp4'}])
