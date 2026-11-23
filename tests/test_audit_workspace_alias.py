from pixelworld.audit import outside_command_paths


def test_workspace_absolute_alias_is_allowed(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    alias = tmp_path / "storage_recovery_alias"
    alias.symlink_to(workspace, target_is_directory=True)
    assert outside_command_paths(f"cd {alias} && python {alias}/generate.py", workspace) == []


def test_unrelated_directory_alias_remains_outside(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "other_experiment"
    outside.mkdir()
    alias = tmp_path / "other_alias"
    alias.symlink_to(outside, target_is_directory=True)
    command = f"python {alias}/answers.py"
    assert outside_command_paths(command, workspace) == [str(alias / "answers.py")]
