"""Keep current Physics-IQ experiments visible in every homepage rebuild."""
import re


def current_experiments():
    return ('<section id="current-experiments"><h2>当前全量实验</h2>'
        '<article class="card feature" id="physics-resolution-entry">'
        '<span class="badge">正在读取实时进度</span><h3>Physics-IQ · 720p / 4K · 24 FPS</h3>'
        '<p class="desc">GPT-6.1-Sol、GPT-5.6-Sol、DeepSeek-V4.1-Flash × Text / Vision。'
        '每组198条，两档共2,376条；均为24 FPS、120帧、5秒。</p>'
        '<div class="facts" id="home-resolution-summary"></div>'
        '<a class="button primary" href="physics_iq_resolution.html">打开全量进度、视频与生成过程 →</a> '
        '<a class="button" href="physics_iq_resolution.html#physics_iq_720p24fps">720p · 1280×720 →</a> '
        '<a class="button" href="physics_iq_resolution.html#physics_iq_4k24fps">4K · 3840×2160 →</a> '
        '<a class="button" href="physics_iq_4k_smoke/index.html">4K 无损像素 smoke →</a>'
        '<p class="note">进度自动刷新。播放预览保留原生尺寸；无损RGB、PNG、NPZ用于原始记录与评测。</p>'
        '</article><h3>当前模型 × 分辨率 × Track</h3>'
        '<div id="home-resolution-models" class="scroll"><p class="note">正在读取各模型的实时进度…</p></div>'
        '</section>')


def integrate_home(text):
    text=re.sub(r'<section id="current-experiments">.*?</section>','',text,flags=re.S)
    for identifier in ['physics-resolution-entry','physics-4k-smoke-entry']:
        text=re.sub(r'<article\b[^>]*id="'+identifier+r'"[^>]*>.*?</article>','',text,flags=re.S)
    marker='<section id="benchmarks">'
    if marker in text:text=text.replace(marker,current_experiments()+marker,1)
    elif '<main>' in text:text=text.replace('<main>','<main>'+current_experiments(),1)
    else:text=current_experiments()+text
    text=text.replace('<h2>主 Benchmark 与进度</h2>','<h2>Benchmark 与已有结果</h2>')
    text=re.sub(r'<section><h2>视频详情同步</h2>.*?</section>','',text,flags=re.S)
    # The old benchmark-wide table is retained as a reference below the active matrix.
    text=re.sub(r'<section><h2>模型 × Benchmark 总览</h2>(.*?)</section>',
        r'<details class="card"><summary>其他 Benchmark 与历史协议的模型总览</summary>\1</details>',text,flags=re.S)
    if 'href="physics_iq_resolution.html">720p / 4K' not in text:
        text=text.replace('<a href="physics_iq_baseline.html">Physics-IQ</a>',
            '<a href="physics_iq_baseline.html">Physics-IQ</a><a href="physics_iq_resolution.html">720p / 4K</a>',1)
    return text


def integrate_physics(text):
    if 'id="physics-resolution-overview-entry"' in text:return text
    card=('<section class="card feature" id="physics-resolution-overview-entry">'
        '<span class="badge">当前全量分辨率实验</span><h2>Physics-IQ · 720p / 4K · 24 FPS</h2>'
        '<p>三个模型 × Text/Vision，每组198条。实时进度、各轮视频、token与耗时在同一入口查看。</p>'
        '<a class="button primary" href="physics_iq_resolution.html">打开分辨率实验总览 →</a></section>')
    return text.replace('<main>','<main>'+card,1)
