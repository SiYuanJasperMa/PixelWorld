from pixelworld.audit import failed_scratch_path


def test_denied_first_loop_save_after_local_inventory():
    body = "for i in [0,12,24]:\n im.save(f'/tmp/pw_inventory_{i:02d}.png')\n"
    command = "find frames -type f | wc -l; ls -lh pixels.npz inspection.png; python - <<'PY'\n" + body + "PY"
    item = {'command': command, 'exit_code': 1,
            'aggregated_output': 'File "<stdin>", line 2\nFileNotFoundError: [Errno 2] No such file or directory: \'/tmp/pw_inventory_00.png\'\n'}
    assert failed_scratch_path('/tmp/pw_inventory_', item)
    assert not failed_scratch_path('/tmp/pw_inventory_', {**item, 'exit_code': 0})
    assert not failed_scratch_path('/tmp/pw_inventory_', {**item, 'command': 'cat /tmp/private; ' + command})
    assert not failed_scratch_path('/tmp/pw_inventory_', {**item, 'command': 'ls /tmp/private; ' + command})
    assert not failed_scratch_path('/tmp/pw_inventory_', {**item, 'command': 'cd frames; ' + command})
