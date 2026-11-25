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


def test_literal_sed_address_is_not_a_path_but_absolute_target_is(tmp_path):
    assert outside_command_paths("sed -i '/before=line/d' generate.py", tmp_path) == []
    assert '/outside/case.py' in outside_command_paths("sed -i '/before=line/d' /outside/case.py", tmp_path)


def test_append_and_compound_python_ignore_only_arithmetic(tmp_path):
    append="cat >> generate.py <<'PY'\na=np.zeros((H//S,W//S))\nPY\n"
    compound="cp generate.py saved.py; python - <<'PY'\ncontent = '''\nshape=(W//step,H//step)\npath='/outside/reference.png'\n'''\nPY\n"
    assert outside_command_paths(append,tmp_path)==[]
    assert outside_command_paths(compound,tmp_path)==['/outside/reference.png']


def test_sed_append_and_multiple_expressions_keep_real_writes_audited(tmp_path):
    append=r"sed -i '/Normal\/=np.maximum/a Normal *= -1' generate.py"
    multiple=r"sed -i 's/def unary(m): return m/def unary(m): return np.asarray(m)/; /reach=smooth(4)/d; /^L=\[sprite(a) for a in leftm\]; R=/s/L=\[sprite(a) for a in leftm\]; //' generate.py"
    assert outside_command_paths(append,tmp_path)==[]
    assert outside_command_paths(multiple,tmp_path)==[]
    assert '/outside/write.log' in outside_command_paths("sed -i '/pattern/s/x/y/w /outside/write.log' generate.py",tmp_path)
    assert '/outside/file.py' in outside_command_paths(append.replace('generate.py','/outside/file.py'),tmp_path)
