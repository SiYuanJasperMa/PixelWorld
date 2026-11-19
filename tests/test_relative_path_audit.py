from pixelworld.audit import outside_command_paths


def test_parent_reference_inside_explicit_workdir_is_allowed(tmp_path):
    workspace=tmp_path/'workspace';sub=workspace/'subdir';sub.mkdir(parents=True)
    command="python3 - <<'PY'\nfrom PIL import Image\nImage.open('../first_frame.png')\nPY"
    assert outside_command_paths(command,workspace,working_directory=sub)==[]
    assert 'relative-parent-traversal' in outside_command_paths(command,workspace)


def test_literal_leading_cd_resolves_parent_inside_workspace(tmp_path):
    workspace=tmp_path/'workspace';(workspace/'subdir').mkdir(parents=True)
    command="cd subdir && python3 - <<'PY'\nopen('../first_frame.png').read()\nPY"
    assert outside_command_paths(command,workspace)==[]


def test_symlink_escape_and_actual_parent_escape_remain_blocked(tmp_path):
    workspace=tmp_path/'workspace';sub=workspace/'subdir';sub.mkdir(parents=True)
    outside=tmp_path/'outside';outside.mkdir();(workspace/'link').symlink_to(outside,target_is_directory=True)
    assert 'relative-parent-traversal' in outside_command_paths("cat ../../secret",workspace,working_directory=sub)
    assert 'relative-parent-traversal' in outside_command_paths("cat ../link/secret",workspace,working_directory=sub)


def test_dynamic_and_untracked_directory_changes_remain_conservative(tmp_path):
    workspace=tmp_path/'workspace';(workspace/'subdir').mkdir(parents=True)
    for command in ["cd $DIR && cat ../first_frame.png", "cd subdir; cat ../first_frame.png",
                    "cd subdir && python3 - <<'PY'\nimport os\nos.chdir('other')\nopen('../first_frame.png')\nPY"]:
        assert 'relative-parent-traversal' in outside_command_paths(command,workspace)
