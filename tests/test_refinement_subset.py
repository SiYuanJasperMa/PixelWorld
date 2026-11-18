import json
import pytest
from pixelworld.util import sha256
from tools.evaluate_refinement_subset import first_deliverable


def test_first_deliverable_skips_completion_only_partial_snapshot(tmp_path):
    run=tmp_path/'run'
    for number,complete in [(0,False),(1,True),(2,True)]:
        folder=run/f'trace/round_{number:04d}';folder.mkdir(parents=True)
        (folder/'pixels.npz').write_bytes(b'saved RGB')
        (folder/'generate.py').write_text('code')
        (folder/'snapshot.json').write_text(json.dumps({'round':number,'required_output_files_complete':complete,
            'pixels_sha256':sha256(folder/'pixels.npz'),'generate_sha256':sha256(folder/'generate.py'),
            'artifact_checks':{'frame0_exact':True,'all_png_tensor_exact':True,'copied_code_and_tensor_match_source':True}}))
    folder,record=first_deliverable(run)
    assert record['round']==1
    (folder/'pixels.npz').write_bytes(b'changed')
    with pytest.raises(ValueError,match='tensor changed'):first_deliverable(run)
