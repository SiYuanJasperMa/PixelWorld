import json
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image

from pixelworld.config import load_config
from pixelworld.flash import install

ASSETS = Path(__file__).resolve().parents[1] / "pixelworld" / "assets"


def _case(tmp_path: Path, frames: int = 4) -> Path:
    source = tmp_path / "case"
    source.mkdir()
    spec = {"task_type": "i2v", "width": 32, "height": 24, "frames": frames, "fps": 4, "audio": None}
    (source / "task_spec.json").write_text(json.dumps(spec))
    (source / "prompt.txt").write_text("A red ball rolls to the right.\n")
    image = np.zeros((24, 32, 3), dtype=np.uint8)
    image[:, :, 0] = 180
    Image.fromarray(image).save(source / "first_frame.png")
    return source


def _workspace(tmp_path: Path, source: Path) -> Path:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    for name in ("prompt.txt", "task_spec.json", "first_frame.png"):
        shutil.copy2(source / name, workspace / name)
    shutil.copy2(ASSETS / "GENERATION_RULES.md", workspace / "GENERATION_RULES.md")
    shutil.copy2(ASSETS / "artifact_io.py", workspace / "artifact_io.py")
    shutil.copy2(ASSETS / "player_template.html", workspace / "player_template.html")
    return workspace


def test_flash_variant_parsing(tmp_path):
    value = {
        "inputs_root": "inputs", "runs_root": "runs", "harness_variant": "flash-v1",
        "models": [{"key": "m", "model": "actual", "tracks": ["vision-agent"]}],
    }
    path = tmp_path / "config.json"
    path.write_text(json.dumps(value))
    assert load_config(path).harness_variant == "flash-v1"


def test_install_writes_summary_and_helpers(tmp_path):
    source = _case(tmp_path)
    workspace = _workspace(tmp_path, source)
    record = install(workspace, json.loads((source / "task_spec.json").read_text()), source)
    summary = json.loads((workspace / "INPUT_SUMMARY.json").read_text())
    assert summary["canvas"] == {"width": 32, "height": 24, "frames": 4, "fps": 4,
                                 "duration_seconds": 1.0}
    assert summary["prompt"].startswith("A red ball")
    assert summary["first_frame"]["width"] == 32
    assert len(summary["first_frame"]["grid_3x3_mean_rgb"]) == 3
    assert "pixels" not in summary
    assert (workspace / "pixelworld_run.py").is_file()
    assert (workspace / "pixelworld_check.py").is_file()
    assert "Harness helpers:" in (workspace / "GENERATION_RULES.md").read_text()
    assert record["input_summary"]["sha256"]


def test_rules_addendum_is_idempotent(tmp_path):
    source = _case(tmp_path)
    workspace = _workspace(tmp_path, source)
    spec = json.loads((source / "task_spec.json").read_text())
    install(workspace, spec, source)
    install(workspace, spec, source)
    assert (workspace / "GENERATION_RULES.md").read_text().count("Harness helpers:") == 1


def _run_check(workspace: Path) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "pixelworld_check.py"], cwd=workspace,
                          capture_output=True, text=True, timeout=120)


def test_check_reports_missing_outputs(tmp_path):
    source = _case(tmp_path)
    workspace = _workspace(tmp_path, source)
    install(workspace, json.loads((source / "task_spec.json").read_text()), source)
    result = _run_check(workspace)
    assert result.returncode == 1
    report = json.loads(result.stdout.strip().splitlines()[-1])
    assert not report["pass"]
    assert any("missing" in problem for problem in report["problems"])


def test_check_accepts_valid_outputs(tmp_path):
    source = _case(tmp_path)
    workspace = _workspace(tmp_path, source)
    install(workspace, json.loads((source / "task_spec.json").read_text()), source)
    sys.path.insert(0, str(workspace))
    try:
        import artifact_io
        frames = np.zeros((4, 24, 32, 3), dtype=np.uint8)
        frames[:, :, :, 0] = 180
        frames[1:, 10:14, 8:12, 1] = 255
        (workspace / "generate.py").write_text("raise SystemExit('fixture')\n")
        artifact_io.save(frames, workspace)
    finally:
        sys.path.remove(str(workspace))
    result = _run_check(workspace)
    report = json.loads(result.stdout.strip().splitlines()[-1])
    assert result.returncode == 0, report
    assert report["pass"] and report["frame0_exact"]


def test_lean_variant_skips_input_summary(tmp_path):
    source = _case(tmp_path)
    workspace = _workspace(tmp_path, source)
    record = install(workspace, json.loads((source / 'task_spec.json').read_text()),
                     source, include_summary=False)
    assert record['input_summary'] is None
    assert not (workspace / 'INPUT_SUMMARY.json').exists()
    rules = (workspace / 'GENERATION_RULES.md').read_text()
    assert 'Harness helpers:' in rules
    assert 'INPUT_SUMMARY.json' not in rules
    assert (workspace / 'pixelworld_run.py').is_file()


def test_compact_api_keeps_exact_backend_and_rules(tmp_path):
    from pixelworld.flash import install_api
    from pixelworld.util import sha256
    source = _case(tmp_path)
    workspace = _workspace(tmp_path, source)
    writer_hash = sha256(workspace / 'artifact_io.py')
    rules_hash = sha256(workspace / 'GENERATION_RULES.md')
    record = install_api(workspace)
    assert record['backend_sha256'] == writer_hash
    assert sha256(workspace / 'GENERATION_RULES.md') == rules_hash
    assert not (workspace / 'player_template.html').exists()
    assert (workspace / '_pixelworld_io/player_template.html').is_file()
    assert not (workspace / 'INPUT_SUMMARY.json').exists()


def test_save_api_preserves_exact_rgb_artifacts(tmp_path):
    from pixelworld.flash import install_api
    source = _case(tmp_path)
    workspace = _workspace(tmp_path, source)
    install_api(workspace, receipt=True)
    script = """import numpy as np
from PIL import Image
import artifact_io
from pathlib import Path
frame = np.asarray(Image.open('first_frame.png').convert('RGB'))
frames = np.repeat(frame[None], 4, axis=0)
frames[1:, 10:14, 8:12, 1] = 255
artifact_io.save(frames, Path('.'))
"""
    (workspace / 'generate.py').write_text(script)
    result = subprocess.run([sys.executable, 'generate.py'], cwd=workspace,
                            text=True, capture_output=True, timeout=60)
    assert result.returncode == 0, result.stderr
    receipt = json.loads(result.stdout)
    assert receipt['written'] == 'pixels.npz' and receipt['frames'] == 4
    with np.load(workspace / 'pixels.npz') as archive:
        pixels = archive['pixels']
    for index in range(4):
        rgb = np.asarray(Image.open(workspace / 'frames' / f'{index:02d}.png'), dtype=np.uint32)
        packed = (rgb[:, :, 0] << 16) | (rgb[:, :, 1] << 8) | rgb[:, :, 2]
        assert np.array_equal(packed, pixels[:, :, index])
    assert (workspace / 'index.html').is_file()


def test_api_reports_invalid_rgb_without_a_false_receipt(tmp_path):
    from pixelworld.flash import install_api
    source = _case(tmp_path)
    workspace = _workspace(tmp_path, source)
    install_api(workspace, receipt=True)
    result = subprocess.run([sys.executable, '-c',
        "import artifact_io, numpy as np; artifact_io.save(np.zeros((1,2,3,3),dtype=np.uint8))"],
        cwd=workspace, text=True, capture_output=True, timeout=60)
    assert result.returncode != 0
    assert not result.stdout.strip()
