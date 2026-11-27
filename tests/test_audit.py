import json
from pathlib import Path

import numpy as np
from PIL import Image

from pixelworld.audit import (canonicalize_tensor, invokes_network, invokes_subagent,
                              outside_command_paths, classify_outside_command_paths, validate,
                              failed_scratch_path, permitted_tool_roots, PLAYWRIGHT_SOFTWARE_CACHE)
from pixelworld.util import sha256


def test_failed_scratch_denial_does_not_hide_existing_or_reference_reads(tmp_path):
    missing = '/tmp/pixelworld_nonexistent_audit_fixture_9e2d/file.py'
    item = {'exit_code':0, 'command':f'cat > {missing}; python generate.py',
            'aggregated_output':f'/bin/bash: line 1: {missing}: Read-only file system\n'}
    assert failed_scratch_path(missing,item)
    assert not failed_scratch_path('/other/case/reference.png',
                                  {'aggregated_output':'/other/case/reference.png: Read-only file system'})
    assert not failed_scratch_path(missing, {'aggregated_output':'unrelated error'})
    assert not failed_scratch_path(missing, {'command':f'python {missing}',
                                            'aggregated_output':f"can't open file '{missing}': No such file or directory"})
    existing = tmp_path/'file.py';existing.write_text('fixture')
    assert not failed_scratch_path(str(existing),{'aggregated_output':str(existing)+': Read-only file system'})


def test_shell_denied_creation_and_missing_python_scratch_is_not_access(tmp_path):
    path='/tmp/pixelworld_denied_creation_fixture_57c8.py'
    item={'exit_code':2,'command':f"cat > {path} <<'PY'\nprint(1)\nPY\npython {path}",
          'aggregated_output':f"/bin/bash: line 1: {path}: No such file or directory\npython: can't open file '{path}': [Errno 2] No such file or directory"}
    assert failed_scratch_path(path,item)
    assert not failed_scratch_path(path,{**item,'command':f'python {path}'})
    assert not failed_scratch_path('/other/case/reference.png',item)
    assert not failed_scratch_path(path,{**item,'aggregated_output':'an unrelated file: No such file or directory'})


def test_uncaught_image_processing_failure_precedes_unexecuted_scratch_outputs():
    path = '/tmp/pixelworld_unexecuted_cv2_scratch_29a1.png'
    command = "python - <<'PY'\nimport cv2\ncv2.grabCut(None)\ncv2.imwrite('"+path+"', mask)\nPY"
    output = 'Traceback (most recent call last):\n  File "<stdin>", line 2, in <module>\ncv2.error: failed image processing\n'
    item = {'command': command, 'exit_code': 1, 'aggregated_output': output}
    assert failed_scratch_path(path, item)
    assert not failed_scratch_path(path, {**item, 'exit_code': 0})
    assert not failed_scratch_path(path, {**item, 'aggregated_output': 'unrelated failure'})
    assert not failed_scratch_path(path, {**item, 'command': command.replace('cv2.grabCut(None)', "cv2.imread('"+path+"')")})
    controlled = command.replace('cv2.grabCut(None)', 'try:\n cv2.grabCut(None)\nexcept Exception:\n pass')
    assert not failed_scratch_path(path, {**item, 'command': controlled})


def test_installed_browser_cache_is_tool_software_only(tmp_path):
    assert outside_command_paths(f'python "{PLAYWRIGHT_SOFTWARE_CACHE}/browser/chrome"',tmp_path)==[]
    assert outside_command_paths(f'cat "{PLAYWRIGHT_SOFTWARE_CACHE}_other/reference.png"',tmp_path)
    assert outside_command_paths('cat /home/user/.cache/other_case/pixels.npz',tmp_path)


def test_failed_literal_cd_is_warning_only_after_explicit_workspace_return(tmp_path):
    workspace=tmp_path/'workspace';workspace.mkdir()
    missing=tmp_path/'misspelled'
    command=f'cd {missing} 2>/dev/null; cd {workspace} && python generate.py'
    blocked,warnings=classify_outside_command_paths(command,workspace)
    assert blocked==[] and warnings==[str(missing)]
    # Existing outside directories and actual file reads remain blocked.
    missing.mkdir()
    assert classify_outside_command_paths(command,workspace)[0]==[str(missing)]
    assert classify_outside_command_paths(f'cat {missing}/reference.png',workspace)[0]
    missing.rmdir()
    assert classify_outside_command_paths(command+f'; cat {missing}/reference.png',workspace)[0]


def make_artifacts(workspace: Path, time_first=False):
    workspace.mkdir()
    frames = []
    yy, xx = np.indices((144, 256), dtype=np.uint16)
    for index in range(20):
        rgb = np.stack([(xx + index) % 256, (yy * 2 + index) % 256,
                        (xx + yy + index) % 256], axis=-1).astype(np.uint8)
        frames.append(rgb)
    frames = np.stack(frames)
    frames[0] = frames[0]
    Image.fromarray(frames[0]).save(workspace / "first_frame.png")
    folder = workspace / "frames"
    folder.mkdir()
    for index, frame in enumerate(frames):
        Image.fromarray(frame).save(folder / f"{index:02d}.png")
    values = frames.astype(np.uint32)
    packed = (values[..., 0] << 16) | (values[..., 1] << 8) | values[..., 2]
    tensor = packed if time_first else packed.transpose(1, 2, 0)
    np.savez_compressed(workspace / "pixels.npz", pixels=tensor)
    for name in ("generate.py", "inspection.png", "transition_check.png", "index.html",
                 "OBSERVATION.md", "METHOD.md"):
        if name.endswith(".png"):
            Image.fromarray(frames[0]).save(workspace / name)
        else:
            (workspace / name).write_text("ok")


def test_versioned_python_arithmetic_is_not_an_outside_path(tmp_path):
    command = "/bin/bash -c \"python3.11 - <<'PY'\nshape=np.stack((xx/60,yy/50),axis=2)\nPY\""
    assert outside_command_paths(command, tmp_path) == []
    real_path = command.replace('shape=np.stack((xx/60,yy/50),axis=2)',
                                "shape=np.stack((xx/60,yy/50),axis=2)\npath='/outside/reference.png'")
    assert outside_command_paths(real_path, tmp_path) == ['/outside/reference.png']
    workspace = tmp_path/'workspace'
    make_artifacts(workspace)
    events = tmp_path/'events.jsonl'
    events.write_text('\n'.join(map(json.dumps, [
        {'type': 'item.completed', 'item': {'type': 'command_execution',
                                          'command': 'python3.11 generate.py', 'exit_code': 0}},
        {'type': 'turn.completed'},
    ])))
    result = validate(workspace, events, 0, 'vision-agent', require_reports=False)
    assert result['pass'] and result['generate_runs'] == 1


def test_interpreter_discovery_allows_only_granted_tool_parent_metadata(tmp_path):
    workspace = tmp_path/'workspace'
    workspace.mkdir()
    runtime = tmp_path/'runtime'
    binary = runtime/'bin'
    binary.mkdir(parents=True)
    command = f"find {runtime} -maxdepth 3 -name 'python*' -type f 2>/dev/null | head -10"
    assert outside_command_paths(command, workspace, [binary]) == []
    assert outside_command_paths(command, workspace) == [str(runtime)]
    assert str(runtime/'secret.key') in outside_command_paths(
        command + f'; cat {runtime}/secret.key', workspace, [binary])
    assert str(runtime) in outside_command_paths(
        f"find {runtime} -maxdepth 3 -name 'python*' -type f -exec cat {{}} \\;", workspace, [binary])
    assert str(runtime) in outside_command_paths(f'find {runtime} -type f', workspace, [binary])
    assert str(runtime) in outside_command_paths(
        f"find {runtime} -maxdepth 3 -name 'python*' -type f | xargs cat", workspace, [binary])
    assert str(runtime) in outside_command_paths(
        "python3.11 - <<'PY'\ntext = '''\n" + command + "\n'''\nPY", workspace, [binary])
    packages = f"find {runtime} -maxdepth 3 -iname '*python*' -o -iname 'cv2*' | head -30"
    assert outside_command_paths(packages, workspace, [binary]) == []
    assert str(runtime) in outside_command_paths(packages.replace('head -30','xargs cat'), workspace, [binary])


def test_literal_library_path_list_checks_each_granted_component(tmp_path):
    workspace = tmp_path/'workspace'
    workspace.mkdir()
    runtime = tmp_path/'runtime'
    library = runtime/'lib'
    packages = runtime/'local/lib/python3.11/dist-packages'
    library.mkdir(parents=True)
    packages.mkdir(parents=True)
    paths = f'{packages}:{library}'
    command = f'PYTHONPATH={paths} python3.11 generate.py'
    assert outside_command_paths(command, workspace, [library,packages]) == []
    assert str(runtime/'secrets') in ''.join(outside_command_paths(
        f'PYTHONPATH={packages}:{runtime}/secrets python3.11 generate.py', workspace, [library,packages]))
    assert paths in outside_command_paths(f'cat "{paths}"', workspace, [library,packages])
    assert str(runtime/'secrets.key') in outside_command_paths(
        command+f'; cat {runtime}/secrets.key', workspace, [library,packages])


def test_audit_recovery_preserves_original_pixels_and_rejects_real_outside_paths(tmp_path):
    from tools.revalidate_audit_failures import recover
    from pixelworld.media import DEFAULT_MEDIA
    run=tmp_path/'run';run.mkdir();workspace=run/'workspace';make_artifacts(workspace)
    logs=run/'logs';logs.mkdir()
    command="cat >> generate.py <<'PY'\nshape=(W//step,H//step)\nPY\n"
    events=[{'type':'item.completed','item':{'type':'command_execution','command':command,'status':'completed','exit_code':0}}, {'type':'turn.completed'}]
    (logs/'events.jsonl').write_text('\n'.join(map(json.dumps,events)))
    manifest={'status':'failed','exit_status':0,'track':'vision-agent','media_contract':DEFAULT_MEDIA,
              'validation':{'pass':False,'planning_and_method_reports_required':False,
                            'outside_workspace_command_paths':['/step,H//step']}}
    path=run/'run_manifest.json';path.write_text(json.dumps(manifest));before=path.read_bytes()
    pixels_before=sha256(workspace/'pixels.npz')
    result=recover(run,apply=True)
    assert result['applied']
    assert sha256(workspace/'pixels.npz')==pixels_before
    assert (logs/'run_manifest_before_audit_revalidation.json').read_bytes()==before
    assert json.loads(path.read_text())['validation']['pass']
    assert not recover(run,apply=True)['applied']
    path.write_bytes(before)
    events[0]['item']['command']='cat /outside/reference.png'
    (logs/'events.jsonl').write_text('\n'.join(map(json.dumps,events)))
    assert not recover(run,apply=False)['eligible']


def relocation_fixture(tmp_path, command_suffix=''):
    import shutil
    from pixelworld.media import DEFAULT_MEDIA
    source = tmp_path/'original'
    source.mkdir()
    make_artifacts(source/'workspace')
    (source/'logs').mkdir()
    events = [{'type':'item.completed','item':{'type':'command_execution',
        'command':f'cd {source / "workspace"} && python generate.py'+command_suffix,
        'status':'completed','exit_code':0}}, {'type':'turn.completed'}]
    (source/'logs/events.jsonl').write_text('\n'.join(map(json.dumps,events)))
    manifest = {'status':'failed','exit_status':0,'track':'vision-agent',
        'workspace':str(source/'workspace'),'media_contract':DEFAULT_MEDIA,
        'validation':{'pass':False,'planning_and_method_reports_required':False,
                      'outside_workspace_command_paths':['/old/workspace']}}
    (source/'run_manifest.json').write_text(json.dumps(manifest))
    copy = tmp_path/'relocated'
    shutil.copytree(source, copy)
    return source, copy


def test_relocated_audit_uses_verified_execution_alias_without_rewriting_raw_events(tmp_path):
    from tools.revalidate_audit_failures import recover
    source, copy = relocation_fixture(tmp_path)
    before = (source/'run_manifest.json').read_bytes()
    events_before = (source/'logs/events.jsonl').read_bytes()
    pixels_before = sha256(copy/'workspace/pixels.npz')
    assert not recover(copy, apply=False)['eligible']
    assert recover(copy, apply=True, original_run=source)['applied']
    assert (source/'run_manifest.json').read_bytes() == before
    assert (copy/'logs/run_manifest_before_audit_revalidation.json').read_bytes() == before
    assert (copy/'logs/events.jsonl').read_bytes() == events_before
    assert sha256(copy/'workspace/pixels.npz') == pixels_before


def test_relocated_alias_projection_keeps_real_outside_access_blocked(tmp_path):
    from tools.revalidate_audit_failures import recover
    source, copy = relocation_fixture(tmp_path, '; cat /other_experiment/reference.png')
    result = recover(copy, apply=True, original_run=source)
    assert not result['eligible']
    assert '/other_experiment/reference.png' in result['remaining_validation']['outside_workspace_command_paths']
    assert json.loads((copy/'run_manifest.json').read_text())['status'] == 'failed'


def test_relocated_alias_projection_rejects_modified_event_history(tmp_path):
    import pytest
    from tools.revalidate_audit_failures import recover
    source, copy = relocation_fixture(tmp_path)
    (copy/'logs/events.jsonl').write_text('{}\n')
    with pytest.raises(ValueError, match='Relocated copy differs'):
        recover(copy, apply=True, original_run=source)


def test_relocated_parent_alias_is_resolved_in_original_execution_directory(tmp_path):
    import shutil
    from tools.revalidate_audit_failures import recover
    source, copy = relocation_fixture(tmp_path)
    command = '/bin/bash -c "ls -ld . ../../original/workspace && python generate.py"'
    events = [{'type':'item.completed','item':{'type':'command_execution',
        'command':command,'status':'completed','exit_code':0}}, {'type':'turn.completed'}]
    (source/'logs/events.jsonl').write_text('\n'.join(map(json.dumps,events)))
    # This is the same original workspace, but outside the relocated copy.
    shutil.copy2(source/'logs/events.jsonl', copy/'logs/events.jsonl')
    assert recover(copy, apply=True, original_run=source)['applied']


def test_relocated_parent_reference_to_other_experiment_stays_blocked(tmp_path):
    import shutil
    from tools.revalidate_audit_failures import recover
    source, copy = relocation_fixture(tmp_path)
    events = [{'type':'item.completed','item':{'type':'command_execution',
        'command':"python generate.py; cat ../../other/reference.png",'status':'completed','exit_code':0}}, {'type':'turn.completed'}]
    (source/'logs/events.jsonl').write_text('\n'.join(map(json.dumps,events)))
    shutil.copy2(source/'logs/events.jsonl', copy/'logs/events.jsonl')
    result = recover(copy, apply=True, original_run=source)
    assert not result['eligible']
    assert 'relative-parent-traversal' in result['remaining_validation']['outside_workspace_command_paths']


def test_denied_scratch_in_successful_compound_command_is_warning(tmp_path):
    workspace=tmp_path/'workspace';make_artifacts(workspace)
    path='/tmp/pixelworld_nonexistent_audit_fixture_9e2d/file.py'
    log=tmp_path/'events.jsonl'
    log.write_text('\n'.join(json.dumps(event) for event in [
        {'type':'item.completed','item':{'type':'command_execution','status':'completed','exit_code':0,
          'command':f'cat > {path}; python generate.py',
          'aggregated_output':f'/bin/bash: line 1: {path}: Read-only file system\n'}},
        {'type':'turn.completed'}]))
    result=validate(workspace,log,0,'text')
    assert result['pass']
    assert result['failed_outside_scratch_operations']==[path]
    assert result['outside_workspace_command_paths']==[]


def test_denied_unexecuted_script_body_does_not_become_an_outside_access(tmp_path):
    from pixelworld.audit import denied_script_body_for_path_audit
    workspace=tmp_path/'workspace';make_artifacts(workspace)
    path='/tmp/pixelworld_absent_body_fixture_e50a/analyze.py'
    command=f"cat > {path} <<'PY'\nimport cv2\ncv2.imwrite('/tmp/unexecuted_edges.png', None)\nPY\npython3 {path}\npython generate.py"
    item={'type':'command_execution','status':'completed','exit_code':0,'command':command,
          'aggregated_output':f"/bin/bash: line 1: {path}: No such file or directory\npython3: can't open file '{path}': [Errno 2] No such file or directory\n"}
    assert '/tmp/unexecuted_edges.png' not in denied_script_body_for_path_audit(item)
    log=tmp_path/'events.jsonl';log.write_text('\n'.join(json.dumps(e) for e in [
        {'type':'item.completed','item':item},{'type':'turn.completed'}]))
    result=validate(workspace,log,0,'text')
    assert result['pass'] and result['outside_workspace_command_paths']==[]
    assert result['failed_outside_scratch_operations']==[path]


def test_unproven_script_body_and_later_outside_access_remain_blocked(tmp_path):
    from pixelworld.audit import denied_script_body_for_path_audit
    path='/tmp/pixelworld_absent_body_fixture_e50a/analyze.py'
    command=f"cat > {path} <<'PY'\nopen('/tmp/possible_output.dat','w')\nPY\ncat /outside/reference.png"
    item={'command':command,'aggregated_output':f'/bin/bash: line 1: {path}: No such file or directory\n'}
    assert '/tmp/possible_output.dat' in denied_script_body_for_path_audit(item)
    item['aggregated_output']+=f"python3: can't open file '{path}': [Errno 2] No such file or directory\n"
    assert '/outside/reference.png' in denied_script_body_for_path_audit(item)


def test_command_classification_is_command_level():
    assert invokes_subagent("codex exec 'child task'")
    assert not invokes_subagent("cat > METHOD.md <<'EOF'\nNo codex exec was used.\nEOF")
    assert invokes_network("curl https://example.com")
    assert not invokes_network("printf 'curl was forbidden' > METHOD.md")
    assert not invokes_network("cat > generate.py <<'PY'\n    curl = scorch * growth\nPY")
    assert not invokes_network("python - <<'PY'\ncurl=0.5\nPY")
    assert not invokes_network("cat > generate.py <<'PY'\n    nc+= (10*gauss(hy-cy-9,2.1))[...,None]\nPY\npython generate.py")
    for operator in ('=', '+=', '-=', '*=', '/=', '//=', '%=', '**=', '&=', '|=', '^=', '<<=', '>>='):
        assert not invokes_network(f"python - <<'PY'\n    nc {operator} values\nPY")
    for command in ('nc example.com 443', 'ncat -v example.com 443',
                    'env HTTPS_PROXY=http://proxy /usr/bin/curl https://example.com',
                    'python -c \"requests.get(\'https://example.com\')\"'):
        assert invokes_network(command)
    workspace = Path("/tmp/workspace")
    assert outside_command_paths("sed -n 1,20p /home/user/.codex/skills/SKILL.md", workspace)
    assert not outside_command_paths("/bin/bash -lc 'python3 generate.py'", workspace)
    assert not outside_command_paths("cat /sys/fs/cgroup/memory.max",workspace)
    assert not outside_command_paths("find . -not -path './candidate/*' -not -path './.git/*'",workspace)
    assert outside_command_paths('cat /candidate/other.png',workspace)


def test_python_division_is_not_an_absolute_file_access(tmp_path):
    assert outside_command_paths("python - <<'PY'\nx=int((r+g+b)/3/26)\nPY",tmp_path)==[]
    assert outside_command_paths("python - <<'PY'\nopen('/3/26').read()\nPY",tmp_path)==['/3/26']
    assert outside_command_paths('cat /3/26',tmp_path)==['/3/26']


def test_python_file_heredoc_tuple_division_preserves_literal_path_checks(tmp_path):
    import shlex
    script="cat > generate.py <<'PY'\ncenter=(x+w//2,y+h//2)\nopen('/other/case/input.png').read()\nPY\npython generate.py"
    assert outside_command_paths('/bin/bash -lc '+shlex.quote(script),tmp_path)==['/other/case/input.png']
    literal="cat > generate.py <<'PY'\nopen('/2,y+h//2').read()\nPY"
    assert outside_command_paths(literal,tmp_path)==['/2,y+h//2']


def test_failed_opencv_writer_does_not_imply_outside_input_read(tmp_path):
    path='/tmp/pixelworld_codec_audit_nonexistent_6139.mp4'
    command=f"python - <<'PY'\nimport cv2\np={path!r}\nw=cv2.VideoWriter(p,0,16,(256,144))\nprint('opened',w.isOpened())\nif w.isOpened():\n c=cv2.VideoCapture(p)\nPY"
    item={'command':command,'exit_code':0,'aggregated_output':'opened False\n'}
    assert failed_scratch_path(path,item)
    # An unguarded reader or a fabricated print is not evidence of failed I/O.
    assert not failed_scratch_path(path,{**item,'command':command.replace(' c=cv2.VideoCapture(p)',' pass\nc=cv2.VideoCapture(p)')})
    assert not failed_scratch_path(path,{**item,'command':command.replace("print('opened',w.isOpened())","print('opened False')")})
    existing=tmp_path/'existing.mp4';existing.write_text('unscored fixture')
    assert not failed_scratch_path(str(existing),{**item,'command':command.replace(path,str(existing))})


def test_empty_or_unexecuted_imageio_probe_preserves_real_read_boundary():
    path='/tmp/pixelworld_empty_writer_audit_nonexistent_6139.gif'
    command=f"python - <<'PY'\nimport imageio.v2 as imageio,os\nw=imageio.get_writer({path!r},mode='I')\nw.close()\nprint(os.path.getsize({path!r}))\nPY"
    item={'command':command,'exit_code':0,'aggregated_output':f"FileNotFoundError [Errno 2] No such file or directory: {path!r}\n"}
    assert failed_scratch_path(path,item)
    assert not failed_scratch_path(path,{**item,'command':command.replace('w.close()','w.append_data(frame)\nw.close()')})
    assert not failed_scratch_path(path,{**item,'command':command.replace('w.close()','open('+repr(path)+',"rb").read()\nw.close()')})
    before=f"python - <<'PY'\nimport imageio\nprint(imageio.config.plugins['FFMPEG'])\ntry:\n print(imageio.get_writer({path!r},mode='I'))\nexcept Exception:pass\nPY"
    error='Traceback (most recent call last):\n  File "<stdin>", line 2, in <module>\nTypeError: module object is not subscriptable\n'
    assert failed_scratch_path(path,{'command':before,'exit_code':1,'aggregated_output':error})


def test_shell_wrapped_python_floor_division_does_not_hide_real_file_paths(tmp_path):
    import shlex
    script = "python - <<'PY'\na = 123/3//23\nprint(a)\nPY"
    assert outside_command_paths('/bin/bash -lc ' + shlex.quote(script), tmp_path) == []
    script = "python - <<'PY'\na = 123/3//23\nopen('/other/case/first_frame.png').read()\nPY"
    assert '/other/case/first_frame.png' in outside_command_paths(
        '/bin/bash -lc ' + shlex.quote(script), tmp_path)


def test_lossless_time_first_canonicalization(tmp_path):
    workspace = tmp_path / "workspace"
    logs = tmp_path / "logs"
    make_artifacts(workspace, time_first=True)
    before = np.load(workspace / "pixels.npz")["pixels"].copy()
    report = canonicalize_tensor(workspace, logs)
    after = np.load(workspace / "pixels.npz")["pixels"]
    assert report and report["rgb_frames_changed"] is False
    assert np.array_equal(after, before.transpose(1, 2, 0))


def test_validation_and_track_visual_boundary(tmp_path):
    workspace = tmp_path / "workspace"
    make_artifacts(workspace)
    clean = tmp_path / "clean.jsonl"
    clean.write_text(json.dumps({"type": "turn.completed"}) + "\n")
    assert validate(workspace, clean, 0, "text")["pass"]
    visual = tmp_path / "visual.jsonl"
    visual.write_text("\n".join([
        json.dumps({"type": "item.completed", "item": {"type": "view_image", "name": "view_image"}}),
        json.dumps({"type": "turn.completed"}),
    ]) + "\n")
    assert not validate(workspace, visual, 0, "text")["pass"]
    assert validate(workspace, visual, 0, "vision-agent")["pass"]


def test_terminal_cli_shutdown_preserves_exit_and_all_other_gates(tmp_path):
    workspace = tmp_path/'workspace'
    make_artifacts(workspace)
    events = tmp_path/'events.jsonl'
    events.write_text(json.dumps({'type':'turn.completed', 'usage':{'output_tokens':123}})+'\n')
    assert not validate(workspace, events, -15, 'text')['pass']
    receipt = {'kind':'cli_shutdown', 'events_sha256':sha256(events),
               'process_exit_status':-15, 'usage_output_tokens':123}
    (tmp_path/'cli_shutdown.json').write_text(json.dumps(receipt))
    result = validate(workspace, events, -15, 'text')
    assert result['pass'] and result['exit_status'] == -15
    assert result['completion_mode'] == 'agent_finished_cli_teardown_recovered'
    assert not validate(workspace, events, -9, 'text')['pass']
    # An unchanged receipt cannot bless any later action or modified log.
    original = events.read_text()
    events.write_text(original+json.dumps({'type':'item.started'})+'\n')
    assert not validate(workspace, events, -15, 'text')['pass']
    events.write_text(original)
    (workspace/'frames/00.png').unlink()
    assert not validate(workspace, events, -15, 'text')['pass']


def test_cli_teardown_wait_stops_only_after_successful_completion(tmp_path, monkeypatch):
    import subprocess
    from pixelworld import runner
    events = tmp_path/'events.jsonl'
    events.write_text(json.dumps({'type':'turn.completed', 'usage':{'output_tokens':123}})+'\n')
    monkeypatch.setattr(runner, 'WATCHDOG_POLL_SECONDS', .01)
    agent = subprocess.Popen(['python', '-c', 'import time; time.sleep(10)'], start_new_session=True)
    exit_status, receipt = runner._wait(agent, events, tmp_path, teardown_grace_seconds=0)
    assert exit_status == -15
    assert receipt['kind'] == 'cli_shutdown'
    assert receipt['process_exit_status'] == exit_status
    assert receipt['events_sha256'] == sha256(events)


def test_shutdown_receipt_binds_raw_events_after_historical_image_enrichment(tmp_path):
    workspace = tmp_path/'workspace'
    make_artifacts(workspace)
    raw, events = tmp_path/'events.raw.jsonl', tmp_path/'events.jsonl'
    terminal = {'type':'turn.completed', 'usage':{'output_tokens':123}}
    raw.write_text(json.dumps(terminal)+'\n')
    enriched = {**terminal, '_harness_received':{'utc':'2026-11-09T00:00:00Z'}}
    events.write_text(json.dumps(enriched)+'\n'+json.dumps({
        'type':'item.completed', 'item':{'type':'full_trace_image_return', 'images':[]}})+'\n')
    receipt = {'kind':'cli_shutdown', 'terminal_source':raw.name, 'terminal_sha256':sha256(raw),
               'process_exit_status':-15, 'usage_output_tokens':123}
    (tmp_path/'cli_shutdown.json').write_text(json.dumps(receipt))
    assert validate(workspace, events, -15, 'vision-agent')['pass']
    assert not validate(workspace, events, -15, 'text')['pass']
    # Real commands, even added to normalized stdout, cannot be filtered away.
    with events.open('a') as handle:
        handle.write(json.dumps({'type':'item.completed', 'item':{'type':'command_execution',
                     'command':'python generate.py', 'exit_code':0}})+'\n')
    assert not validate(workspace, events, -15, 'vision-agent')['pass']


def test_frame_zero_is_checked_against_frozen_input(tmp_path):
    workspace = tmp_path / "workspace"
    make_artifacts(workspace)
    original = tmp_path / "input_snapshot" / "first_frame.png"
    original.parent.mkdir()
    with Image.open(workspace / "first_frame.png") as im:
        im.save(original)
    events = tmp_path / "events.jsonl"
    events.write_text(json.dumps({"type": "turn.completed"}) + "\n")
    assert validate(workspace, events, 0, "text")["pass"]
    # Altering the workspace input must not redefine the original I2V anchor.
    with Image.open(original) as im:
        changed = np.asarray(im.convert("RGB")).copy()
    changed[0, 0] ^= 255
    Image.fromarray(changed).save(workspace / "first_frame.png")
    assert validate(workspace, events, 0, "text")["pass"]
    Image.fromarray(changed).save(workspace / "frames" / "00.png")
    result = validate(workspace, events, 0, "text")
    assert result["frame0_exact"] is False
    assert result["pass"] is False


def test_image_return_hidden_by_stdout_projection_enforces_track(tmp_path):
    workspace=tmp_path/'workspace';make_artifacts(workspace)
    events=tmp_path/'events.jsonl'
    events.write_text('\n'.join([json.dumps({'type':'turn.completed'}),
        json.dumps({'type':'item.completed','item':{'type':'full_trace_image_return',
            'call_id':'nested-image','returned_images':[{'path':'tool_images/a.png'}]}})])+'\n')
    assert not validate(workspace,events,0,'text')['pass']
    assert not validate(workspace,events,0,'vision-input')['pass']
    assert validate(workspace,events,0,'vision-agent')['pass']


def test_failed_patch_request_does_not_claim_successful_external_write(tmp_path):
    workspace=tmp_path/'workspace';make_artifacts(workspace)
    events=tmp_path/'events.jsonl';outside=str(tmp_path/'missing/generate.py')
    events.write_text('\n'.join([json.dumps({'type':'item.completed','item':{'type':'file_change','status':'failed','changes':[{'path':outside,'kind':'update'}]}}),json.dumps({'type':'turn.completed'})])+'\n')
    result=validate(workspace,events,0,'vision-agent')
    assert result['pass'] and result['outside_workspace_file_changes']==[]
    assert result['failed_outside_workspace_file_change_attempts']==[outside]


def test_denied_scratch_image_write_is_not_an_outside_access():
    path = '/tmp/pixelworld_denied_montage_nonexistent_6139.jpg'
    command = (f"python - <<'PY'\nfrom PIL import Image\n"
               f"out=Image.new('RGB',(32,32))\nout.save({path!r})\nPY")
    item = {'command': command, 'exit_code': 1,
            'aggregated_output': 'Traceback (most recent call last):\n'
                                 '  File "<stdin>", line 3, in <module>\n'
                                 f'FileNotFoundError: [Errno 2] No such file or directory: {path!r}\n'}
    assert failed_scratch_path(path, item)
    read = f"python - <<'PY'\nfrom PIL import Image\nImage.open({path!r})\nPY"
    assert not failed_scratch_path(path, {**item, 'command': read})
    later = (f"python - <<'PY'\nfrom PIL import Image\n"
             f"out=Image.new('RGB',(32,32))\nout.save({path!r})\nprint(undefined)\nPY")
    assert not failed_scratch_path(path, {**item, 'command': later,
        'aggregated_output': 'Traceback (most recent call last):\n'
                             '  File "<stdin>", line 4, in <module>\n'
                             'NameError: name undefined is not defined\n'})


def test_permitted_tool_roots_waive_only_granted_read_roots(tmp_path):
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    tool = tmp_path / 'runtime/bin'
    tool.mkdir(parents=True)
    secret = tmp_path / 'runtime/queue_proxy_logs'
    secret.mkdir(parents=True)
    (workspace.parent / 'permissions_profile.json').write_text(json.dumps({
        'filesystem': {':minimal': 'read', str(workspace): 'write', str(tool): 'read'}}))
    roots = permitted_tool_roots(workspace)
    assert roots == [tool.resolve()]
    assert outside_command_paths(f'{tool}/ffmpeg -version', workspace, roots) == []
    assert outside_command_paths(f'{secret}/local.key', workspace, roots) == [f'{secret}/local.key']
    # Without the profile-derived roots the tool path stays blocked.
    assert outside_command_paths(f'{tool}/ffmpeg -version', workspace) == [f'{tool}/ffmpeg']


def test_denied_first_literal_loop_scratch_write_is_a_warning():
    from pixelworld.audit import failed_scratch_path
    prefix = '/tmp/pixelworld_missing_loop_613948/'
    command = "python - <<'PY'\nfrom PIL import Image\nfor name,box in [('ball',(0,0,4,4)),('block',(1,1,4,4))]:\n    Image.open('first_frame.png').crop(box).save('" + prefix + "'+name+'.png')\nPY"
    item = {'command': command, 'exit_code': 1,
        'aggregated_output': 'Traceback (most recent call last):\n  File "<stdin>", line 3, in <module>\nFileNotFoundError: [Errno 2] No such file or directory: ' + repr(prefix+'ball.png')}
    assert failed_scratch_path(prefix, item)
    assert not failed_scratch_path(prefix, {**item, 'aggregated_output':
        item['aggregated_output'].replace('ball.png','block.png')})
    assert not failed_scratch_path(prefix, {**item, 'exit_code': 0})
    prior_read = command.replace("Image.open('first_frame.png')", "Image.open('"+prefix+"'+name+'.png')")
    assert not failed_scratch_path(prefix, {**item, 'command': prior_read})


def test_unknown_dynamic_scratch_name_stays_blocked():
    from pixelworld.audit import failed_scratch_path
    item = {'command': "python - <<'PY'\nimage.save('/tmp/'+unknown+'.png')\nPY", 'exit_code': 1,
        'aggregated_output': '  File "<stdin>", line 1, in <module>\nFileNotFoundError: [Errno 2] No such file or directory: "/tmp/pixelworld_unknown_613948.png"'}
    assert not failed_scratch_path('/tmp/', item)


def test_denied_first_loop_write_after_builtin_lookups_is_a_warning():
    prefix = '/tmp/pixelworld_missing_lookup_loop_613948/'
    body = "python - <<'PY'\nfrom PIL import Image\nfor name,box in [('ball',(0,0,4,4)),('block',(1,1,4,4))]:\n    Image.open('first_frame.png').crop(box).save('" + prefix + "'+name+'.png')\nPY"
    output = '  File "<stdin>", line 3, in <module>\nFileNotFoundError: [Errno 2] No such file or directory: ' + repr(prefix+'ball.png')
    item = {'command':'command -v ffmpeg; command -v convert; '+body,
            'exit_code':1,'aggregated_output':output}
    assert failed_scratch_path(prefix, item)
    assert not failed_scratch_path(prefix, {**item,'aggregated_output':output.replace('ball.png','block.png')})
    # Only the lookup preamble is exempt; no arbitrary shell action is removed.
    assert not failed_scratch_path(prefix, {**item,'command':'cat '+prefix+'secret; '+body})
    assert not failed_scratch_path(prefix, {**item,'command':'command -v ffmpeg; cat '+prefix+'secret; '+body})


def test_denied_ffmpeg_image_crop_and_short_circuited_crop_are_warnings():
    first='/tmp/pixelworld_denied_ffmpeg_first_613948.png'
    second='/tmp/pixelworld_denied_ffmpeg_second_613948.png'
    crop=lambda path:"ffmpeg -y -loglevel error -i first_frame.png -vf 'crop=10:10:0:0,scale=20:20' "+path
    item={'command':crop(first)+' && '+crop(second),'exit_code':251,
          'aggregated_output':'[image2 @ 0x123] Could not open file : '+first+'\n'}
    assert failed_scratch_path(first,item)
    assert failed_scratch_path(second,item)
    assert not failed_scratch_path(first,{**item,'exit_code':0})
    assert not failed_scratch_path(second,{**item,'command':item['command'].replace(' && ','; ')})
    assert not failed_scratch_path(first,{**item,'command':item['command'].replace('first_frame.png',first)})
    assert not failed_scratch_path(first,{**item,'aggregated_output':item['aggregated_output'].replace(first,second)})
    assert not failed_scratch_path(first,{**item,'command':crop(first)+' && cat '+first})
    assert not failed_scratch_path(first,{**item,'command':item['command'].replace('scale=20:20','movie=/tmp/reference.png')})


def test_denied_first_fstring_scratch_crop_is_not_an_access():
    prefix='/tmp/pixelworld_fstring_missing_crop_83418'
    command="python - <<'PY'\nfrom PIL import Image\nfor i in [1,12,17]:\n im=Image.open(f'frames/{i:02d}.png')\n im.save(f'"+prefix+"{i}.png')\nPY"
    item={'command':command,'exit_code':1,'aggregated_output':
        '  File "<stdin>", line 4, in <module>\nFileNotFoundError: [Errno 2] No such file or directory: '+repr(prefix+'1.png')}
    assert failed_scratch_path(prefix,item)
    assert not failed_scratch_path(prefix,{**item,'aggregated_output':item['aggregated_output'].replace('1.png','12.png')})
    assert not failed_scratch_path(prefix,{**item,'command':command.replace('for i in [1,12,17]:','for i in dynamic_indices:')})
