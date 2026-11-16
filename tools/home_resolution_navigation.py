"""Keep current Physics-IQ experiments visible in every homepage rebuild."""
import re


def current_experiments():
    return ('<section id="current-experiments"><h2>当前主实验</h2>'
        '<article class="card feature" id="physics-resolution-entry">'
        '<span class="badge">正在读取实时进度</span><h3>Physics-IQ · 720p / 24 FPS · 主实验</h3>'
        '<p class="desc">GPT-6.1-Sol、GPT-5.6-Sol、DeepSeek-V4.1-Flash × Text / Vision。'
        '每组198条，共1,188条；24 FPS、120帧、5秒。256×144 与 4K 仅作为分辨率消融。</p>'
        '<div class="facts" id="home-resolution-summary"></div>'
        '<a class="button primary" href="physics_iq_resolution.html">打开主实验进度、视频与 trace →</a> '
        '<a class="button" href="physics_iq_baseline.html">256×144 基线 →</a> '
        '<a class="button" href="physics_iq_resolution.html#resolution-ablation">4K / 分辨率消融 →</a>'
        '<p class="note">进度自动刷新。播放预览保留原生尺寸；无损RGB、PNG、NPZ用于原始记录与评测。</p>'
        '</article><h3>720p 主实验 · 模型 × Track</h3>'
        '<div id="home-resolution-models" class="scroll"><p class="note">正在读取各模型的实时进度…</p></div>'
        '</section>')


def resolution_body():
    """Stable main-experiment-first structure for the live resolution page."""
    return ('<style>'
        '.pw-progress{height:7px;background:#0b1118;border-radius:999px;overflow:hidden;margin:9px 0 7px}'
        '.pw-progress>span{display:block;height:100%;background:linear-gradient(90deg,#78b86a,#b8ed94);border-radius:inherit}'
        '.pw-kpis{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin:18px 0}'
        '.pw-kpi{background:#101720;border:1px solid var(--line);border-radius:10px;padding:14px}'
        '.pw-kpi strong{display:block;font-size:24px;line-height:1.25;font-variant-numeric:tabular-nums}'
        '.pw-kpi span{font-size:11px;color:var(--muted)}'
        '#resolution-latest-grid video{width:100%;aspect-ratio:16/9;background:#070b10;display:block}'
        '#resolution-latest-grid .sample-body{padding:14px 16px}.pw-cell{min-width:230px}.pw-cell strong{font-size:17px}'
        '@media(max-width:700px){.pw-kpis{grid-template-columns:repeat(2,1fr)}}</style>'
        '<p><a class="back" href="/pixelworld/index.html">← PixelWorld 总览</a></p>'
        '<section class="hero"><p class="eyebrow">PHYSICS-IQ · MAIN EXPERIMENT</p>'
        '<h1>720p · 24 FPS Pixel World Models</h1>'
        '<p>三个 coding agents，在 Text 与 Vision 两种条件下完成全部198条官方 Physics-IQ I2V任务。'
        '本页先展示主实验进度与视频；低分辨率和4K结果收纳在后面的消融区域。</p></section>'
        '<section class="card feature" id="resolution-main-overview">'
        '<span class="badge">主实验 · 实时更新</span><h2>1280×720 · 24 FPS · 120帧 · 5秒</h2>'
        '<div id="resolution-main-summary" class="pw-kpis"><p class="note">正在读取实时进度…</p></div>'
        '<div class="actions"><a class="button primary" href="#resolution-main-matrix">模型 × Track 进度 →</a>'
        '<a class="button" href="#resolution-latest">最新视频 →</a>'
        '<a class="button" href="#live-evaluation">官方评测 →</a></div></section>'
        '<section id="resolution-main-matrix"><div class="section-head"><h2>模型 × Track</h2>'
        '<p>点击任意格查看该组全部视频、候选版本和实际工具记录。</p></div>'
        '<div id="resolution-main-table" class="scroll"><p class="note">正在读取六个 cohort…</p></div></section>'
        '<section id="resolution-latest"><div class="section-head"><h2>最近同步的视频</h2>'
        '<p>每个模型与 Track 各显示一条可直接播放的成功结果。</p></div>'
        '<div id="resolution-latest-grid" class="video-grid"><p class="note">正在载入视频预览…</p></div></section>'
        '<section id="live-evaluation" class="card"><h2>官方评测</h2><p class="note">完整组生成后自动运行官方 evaluator。</p></section>'
        '<details class="report" id="resolution-ablation"><summary>分辨率消融 · 256×144 / 720p / 4K</summary>'
        '<p>主实验固定为720p/24 FPS。256×144/4 FPS用于低成本基线；4K/24 FPS用于研究分辨率扩展、Token摊销和执行成本。</p>'
        '<div class="actions" style="margin:0 20px 18px"><a class="button" href="physics_iq_baseline.html">256×144 基线 →</a>'
        '<a class="button" href="physics_iq_4k_smoke/index.html">4K smoke →</a></div>'
        '<div id="resolution-ablation-4k" style="margin:0 18px 18px"><p class="note">正在读取4K进度…</p></div>'
        '<div id="resolution-token-time" style="margin:0 18px 18px"><p class="note">正在读取分辨率成本统计…</p></div>'
        '</details>'
        '<section class="card" id="resolution-protocol"><h2>结果口径</h2>'
        '<p>有效交付要求首帧约束、120张PNG与无损RGB tensor完全一致。基础设施中断与模型失败分开统计；'
        'output_tokens不重复叠加reasoning tokens。所有候选、失败记录和trace均保留。</p></section>')


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
    text=text.replace('href="physics_iq_resolution.html">720p / 4K</a>',
                      'href="physics_iq_resolution.html">720p Main</a>')
    if 'href="physics_iq_resolution.html">720p Main' not in text:
        text=text.replace('<a href="physics_iq_baseline.html">Physics-IQ</a>',
            '<a href="physics_iq_baseline.html">Physics-IQ</a><a href="physics_iq_resolution.html">720p Main</a>',1)
    return text


def integrate_physics(text):
    if 'id="physics-resolution-overview-entry"' in text:return text
    card=('<section class="card feature" id="physics-resolution-overview-entry">'
        '<span class="badge">当前主实验</span><h2>Physics-IQ · 720p · 24 FPS</h2>'
        '<p>三个模型 × Text/Vision，每组198条。实时进度、各轮视频、token与耗时在同一入口查看；4K作为分辨率消融。</p>'
        '<a class="button primary" href="physics_iq_resolution.html">打开720p主实验 →</a></section>')
    return text.replace('<main>','<main>'+card,1)
