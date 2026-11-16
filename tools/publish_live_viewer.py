"""Publish only the live viewer and its cache versions on the Pages branch."""
import argparse
import fcntl
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def publish(checkout: Path):
    checkout = checkout.resolve()
    with checkout.with_name(checkout.name + '.publish.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        identity = json.loads((checkout/'.git/pixelworld-delivery.json').read_text())
        branch = subprocess.check_output(['git','symbolic-ref','--short','HEAD'], cwd=checkout, text=True).strip()
        if identity != {'repository':'SiyuanMaCS/SiyuanMaCS.github.io','branch':'pixelworld-pages'} or branch != identity['branch']:
            raise ValueError('Use the dedicated PixelWorld Pages delivery checkout')
        if subprocess.check_output(['git','status','--porcelain'], cwd=checkout, text=True).strip():
            raise ValueError('Finish the current Pages changes before a viewer-only publication')
        script = (ROOT/'tools/live_refresh.js').read_bytes()
        version = hashlib.sha256(script).hexdigest()[:12]
        (checkout/'pixelworld/live_refresh.js').write_bytes(script)
        changed = ['pixelworld/live_refresh.js']
        from tools.build_bench_review import document
        gallery = 'pixelworld/phygen_pixelspace_smoke/index.html'
        (checkout/gallery).write_text(document('PhyGenBench T2V / I2V Smoke',
            '<section class="hero"><p class="eyebrow">PIXELWORLD · PAIRED SMOKE</p>'
            '<h1>PhyGenBench · T2V / I2V</h1>'
            '<p>相同物理 prompt，分别测试 T2V、I2V 与 Text、Vision。'
            'I2V 首帧来自 Qwen-Image-2.1；后续帧均由 coding agent 生成。</p></section>'
            '<p><a href="/pixelworld/index.html">← PixelWorld 总览</a> · '
            '<a href="/pixelworld/phygen_qwen_init/index.html">Qwen 初始帧 →</a> · '
            '<a href="/pixelworld/phygen_pixelspace.html">协议与进度 →</a></p>'
            '<p id="live-poll-time" class="note">正在读取当前模型与四个条件的进度…</p>'))
        changed.append(gallery)
        from tools.home_resolution_navigation import resolution_body,standard_benchmark_body
        resolution='pixelworld/physics_iq_resolution.html'
        (checkout/resolution).write_text(document('Physics-IQ · 720p / 24 FPS · Main Experiment',
                                                  resolution_body()))
        changed.append(resolution)
        standards = {
            'pixelworld/robojudge.html': ('RoboJudge · 720p / 24 FPS','ROBOJUDGE · MAIN EXPERIMENT','robojudge_main720p24fps','50个shared embodied prompts，保持原始画幅，24 FPS、120帧、5秒。','旧256宽/4 FPS结果与额外模型保留为Legacy，不进入主分数。'),
            'pixelworld/pcve_rigid.html': ('PCVE-RigidBench · Standard 720p','PCVE-RIGIDBENCH · MAIN EXPERIMENT','pcve_standard720p24fps','官方原生1280×720、24 FPS、96帧、4秒；保持编辑生效帧与逐帧评测时间轴。','旧256×144结果保留为Legacy，不继续补旧协议。'),
            'pixelworld/phygen_pixelspace.html': ('PhyGenBench · 720p / 24 FPS','PHYGENBENCH · MAIN EXPERIMENT','phygenbench_t2v_main720p24fps','官方160条T2V prompts，1280×720、24 FPS、120帧、5秒；paired I2V准备完成后加入同页。','旧256×144/4 FPS smoke和pre-v2结果保留为Legacy。'),
        }
        for relative,(title,eyebrow,suite,protocol,legacy) in standards.items():
            (checkout/relative).write_text(document(title,
                standard_benchmark_body(title,eyebrow,suite,protocol,legacy)))
            changed.append(relative)
        smoke='pixelworld/physics_iq_4k_smoke/index.html';(checkout/smoke).parent.mkdir(parents=True,exist_ok=True)
        (checkout/smoke).write_text(document('Physics-IQ · GPT-6.1-Sol · 4K Smoke',
            '<section class=hero><p class=eyebrow>PHYSICS-IQ · NATIVE 4K SMOKE</p><h1>GPT-6.1-Sol · 4K Smoke</h1>'
            '<p>3840×2160 · 24 FPS · 120 帧 · 5 秒。正在读取已通过格式检查的原生像素、代码与工具记录。</p></section>'
            '<p id="live-poll-time">正在读取4K smoke…</p>'))
        changed.append(smoke)
        for name in ['pixelworld/index.html','pixelworld/pixelworld-index.html']:
            p=checkout/name
            if p.exists():
                text=p.read_text()
                if 'id="physics-resolution-entry"' not in text:
                    card='<article class="card feature" id="physics-resolution-entry"><span class="badge">主实验</span><h3>Physics-IQ · 720p / 24 FPS · 主实验</h3><p>三模型 × Text/Vision，每组198条；4K作为分辨率消融。</p><a class="button" href="physics_iq_resolution.html">打开主实验与实时进度 →</a></article>'
                    p.write_text(text.replace('</main>',card+'</main>'));changed.append(name)
        from tools.home_resolution_navigation import integrate_home,integrate_physics
        for name in ['pixelworld/index.html','pixelworld/pixelworld-index.html']:
            p=checkout/name
            if p.exists():p.write_text(integrate_home(p.read_text()));changed.append(name)
        p=checkout/'pixelworld/physics_iq_baseline.html'
        if p.exists():p.write_text(integrate_physics(p.read_text()));changed.append('pixelworld/physics_iq_baseline.html')
        paths = subprocess.check_output(['git','ls-files','--','pixelworld'], cwd=checkout, text=True).splitlines()
        for relative in paths:
            if not relative.endswith('.html'):
                continue
            path = checkout/relative
            text = path.read_text()
            updated = re.sub(r'/pixelworld/live_refresh\.js(?:\?[^\s\"\x27<>]*)?',
                             '/pixelworld/live_refresh.js?v='+version, text)
            if updated != text:
                path.write_text(updated)
                changed.append(relative)
        for start in range(0,len(changed),1000):
            subprocess.run(['git','add','--',*changed[start:start+1000]], cwd=checkout, check=True)
        if subprocess.run(['git','diff','--cached','--quiet'], cwd=checkout).returncode:
            subprocess.run(['git','commit','-m','Refresh live viewer '+version], cwd=checkout, check=True)
        subprocess.run(['git','push','origin',branch], cwd=checkout, check=True)
        return {'script_sha256':hashlib.sha256(script).hexdigest(), 'updated_files':len(changed),
                'commit':subprocess.check_output(['git','rev-parse','HEAD'], cwd=checkout, text=True).strip()}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--checkout',type=Path,required=True)
    print(json.dumps(publish(parser.parse_args().checkout)))
