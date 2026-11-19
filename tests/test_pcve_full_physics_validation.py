from tools.evaluate_pcve_full import incomplete_physics_cases


def row(**changes):
    return dict({'global_id':'scene/edit','physics_status':'completed',
                 'pes':.2,'traj_px':10.,'mask_iou':.3},**changes)


def test_tracker_failure_cannot_be_counted_as_full_physics_score():
    assert incomplete_physics_cases([row(physics_status='tracker_failed')])==['scene/edit']


def test_missing_or_nonfinite_pes_cannot_be_silently_omitted_from_mean():
    for value in (None,float('nan'),float('inf'),True):
        assert incomplete_physics_cases([row(pes=value)])==['scene/edit']
    assert incomplete_physics_cases([row()])==[]
