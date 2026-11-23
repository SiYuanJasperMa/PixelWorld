from pixelworld.audit import outside_command_paths


def test_literal_sed_edit_does_not_join_delimited_operands(tmp_path):
    command = r"sed -i 's/u,v,w = local\/BD/u,v,w = np.moveaxis(local\/BD, -1, 0)/' generate.py"
    assert outside_command_paths(command + '\npython generate.py', tmp_path) == []


def test_sed_absolute_target_and_write_flag_still_audited(tmp_path):
    assert '/outside/case.py' in outside_command_paths("sed -i 's/x/y/' /outside/case.py", tmp_path)
    assert '/outside/case.log' in outside_command_paths("sed -i 's/x/y/w /outside/case.log' task.py", tmp_path)


def test_embedded_literal_python_operators_preserve_real_absolute_paths(tmp_path):
    command = "python - <<'PY'\ncontent = '''\nshape = (W//3,H//3)\npath = '/outside/case.py'\n'''\nPY\n"
    assert outside_command_paths(command, tmp_path) == ['/outside/case.py']
