"""Build a score-free static dashboard that reads real Flash results from HF."""
import argparse
import hashlib
from pathlib import Path

SCRIPT = r'''(()=>{
const HF='https://huggingface.co/datasets/HuggingFriends/PixelWorld/resolve/main/analyses/flash/';
let cohort='confirmation',report;
const $=id=>document.getElementById(id),esc=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmt=(x,n=2)=>typeof x==='number'?x.toFixed(n):'—',tok=x=>typeof x==='number'?(x/1000).toFixed(1)+'k':'—';
async function load(){const path=cohort+'.json';let urls=[HF+path];if(!document.body.dataset.liveBase)urls.unshift('flash_'+path);let error;for(const url of urls){try{let r=await fetch(url+'?t='+Date.now(),{cache:'no-store',signal:AbortSignal.timeout(12000)});if(!r.ok)throw Error('HTTP '+r.status);return await r.json()}catch(e){error=e}}throw error}
function chart(arms){const pts=arms.filter(a=>a.scored_cases===a.expected_cases&&typeof a.mean_cost_normalized70==='number'&&typeof a.mean_v1==='number');if(!pts.length){$('chart').innerHTML='<p class="muted">等待完整匹配评分。这里不展示占位分数。</p>';return}const W=850,H=340,L=70,T=25,R=30,B=55,xmax=Math.max(...pts.map(a=>a.mean_cost_normalized70))*1.15,ymax=Math.max(.3,...pts.map(a=>a.mean_v1))*1.2;let s='<svg viewBox="0 0 '+W+' '+H+'" role="img" aria-label="Measured cost and subset quality">';for(let i=0;i<=4;i++){let y=H-B-(H-B-T)*i/4;s+='<line x1="'+L+'" y1="'+y+'" x2="'+(W-R)+'" y2="'+y+'" stroke="#263241"/><text x="'+(L-8)+'" y="'+(y+4)+'" text-anchor="end" fill="#a0adbc" font-size="12">'+fmt(ymax*i/4)+'</text>';let x=L+(W-R-L)*i/4;s+='<text x="'+x+'" y="'+(H-B+20)+'" text-anchor="middle" fill="#a0adbc" font-size="12">$'+fmt(xmax*i/4)+'</text>'}const colors=['#94ccff','#b8ed94','#c3a8fa','#f4cb91','#ffaaa0'];pts.forEach((a,i)=>{let x=L+a.mean_cost_normalized70/xmax*(W-L-R),y=H-B-a.mean_v1/ymax*(H-T-B);s+='<circle cx="'+x+'" cy="'+y+'" r="7" fill="'+colors[i%colors.length]+'"><title>'+esc(a.label)+' · '+a.scored_cases+' cases · $'+fmt(a.mean_cost_normalized70)+' · IoU '+fmt(a.mean_v1,4)+'</title></circle><text x="'+(x+11)+'" y="'+(y-10)+'" fill="'+colors[i%colors.length]+'" font-size="12">'+esc(a.label)+'</text>'});s+='<text x="'+(W/2)+'" y="'+(H-8)+'" text-anchor="middle" fill="#a0adbc" font-size="13">Cost / fully measured attempt · 70% cache scenario</text><text x="18" y="'+H/2+'" transform="rotate(-90 18 '+H/2+')" text-anchor="middle" fill="#a0adbc" font-size="13">Subset weighted spatial IoU v1 ↑</text></svg>';$('chart').innerHTML=s}
function render(){const arms=report.arms||[];$('scope').textContent=report.scope;$('sync').textContent='更新：'+new Date(report.updated_utc).toLocaleString();$('condition').textContent=cohort==='confirmation'?'工作中的视觉工具 · 20 个独立场景 · 正式 Flash 版本待确认':'旧开发条件：附带首帧，但输出图片工具因 helper 校验失败而不可用。此处数据不作为完整 Vision 主评测。';$('rows').innerHTML=arms.map(a=>{let c=a.counts||{},q=a.paired?.v1;return'<tr><td><b>'+esc(a.label)+'</b></td><td>'+a.valid_rgb+'/'+a.expected_cases+' RGB<br><span class="small muted">'+(c.running||0)+' 运行 · '+a.pending+' 待生成</span></td><td>'+tok(a.mean_input)+' / '+tok(a.mean_output)+'<br><span class="small muted">'+a.usage_measured_cases+' 次完整 usage</span></td><td>$'+fmt(a.mean_cost_normalized70)+'<br><span class="small muted">实测缓存：$'+fmt(a.mean_cost_measured_cache)+'</span></td><td>'+a.scored_cases+'/'+a.expected_cases+'<br>'+fmt(a.mean_v1,4)+' / '+fmt(a.mean_v2,4)+'</td><td>'+(q?fmt(q.relative_mean_delta*100,1)+'%<br><span class="small muted">Δ CI ['+fmt(q.ci95[0],4)+', '+fmt(q.ci95[1],4)+']</span>':'—')+'</td></tr>'}).join('');$('trace').innerHTML=arms.map(a=>{let t=a.trace||{};return'<tr><td>'+esc(a.label)+'</td><td>'+fmt(t.mean_exec_calls,1)+'</td><td>'+fmt(t.mean_image_calls,1)+'</td><td>'+fmt(t.mean_returned_image_messages,1)+'</td><td>'+fmt(t.mean_image_helper_failures,1)+'</td><td>'+(t.source_reads?.['artifact_io.py']??'—')+'</td><td>'+(t.source_reads?.['player_template.html']??'—')+'</td></tr>'}).join('');chart(arms)}
async function poll(){try{report=await load();render()}catch(e){$('sync').textContent='报告同步等待：'+e.message}}document.querySelectorAll('[data-cohort]').forEach(b=>b.onclick=()=>{cohort=b.dataset.cohort;poll()});poll();setInterval(poll,30000)
})();'''


def build(destination):
    destination.mkdir(parents=True, exist_ok=True)
    version = hashlib.sha256(SCRIPT.encode()).hexdigest()[:12]
    (destination / 'flash.js').write_text(SCRIPT)
    (destination / 'flash.html').write_text('''<!doctype html><html lang="zh-CN"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>PixelWorld-Flash · Cost and Quality</title><link rel="stylesheet" href="formal.css">
<style>button{background:#202b37;color:#edf2f7;border:1px solid #425064;padding:8px 13px;border-radius:7px;cursor:pointer;margin-right:8px}svg{width:100%;height:auto}.panel{padding:20px;border:1px solid #2b3745;border-radius:12px;background:#141b24;margin:20px 0}</style></head><body>
<header><nav><strong><a href="index.html">PixelWorld</a></strong><span><a href="index.html">主实验</a><a href="flash.html">Flash</a><a href="https://github.com/SiyuanMaCS/PixelWorld">GitHub</a><a href="https://huggingface.co/datasets/HuggingFriends/PixelWorld">视频与 Trace</a></span></nav></header>
<main><p class="eyebrow">PHYSICS-IQ · HARNESS ANALYSIS</p><h1>PixelWorld-Flash</h1>
<p class="muted">GPT-5.6-Luna · 720p / 24 FPS · 1M output-token budget</p>
<p><button data-cohort="confirmation">独立验证</button><button data-cohort="legacy">旧开发消融</button></p>
<p id="condition" class="notice"></p><p id="scope" class="small muted"></p><p id="sync" class="small muted">正在加载…</p>
<div class="table"><table><thead><tr><th>设置</th><th>生成</th><th>Input / Output 均值</th><th>成本估算</th><th>评分数 · IoU v1 / v2</th><th>配对 v1 变化</th></tr></thead><tbody id="rows"></tbody></table></div>
<section class="panel"><h2>成本与质量</h2><div id="chart"></div><p class="small muted">只绘制已完成全部匹配评分的条件。子集指标不能当作完整 Physics-IQ Verified 分数；置信区间与独立验证完成后再确定正式版本。</p></section>
<details><summary>Trace 中的实际行为</summary><div class="table"><table><thead><tr><th>设置</th><th>exec / case</th><th>图片调用 / case</th><th>真实图片返回 / case</th><th>helper 失败 / case</th><th>读 artifact_io.py</th><th>读 player template</th></tr></thead><tbody id="trace"></tbody></table></div></details>
<footer>成本采用公开列明的参考费率：input $10/M、cached input $1/M、output $50/M。70% 缓存是假设场景；测试模型免费，表中估算不是实际账单。原始协议状态、RGB 有效性和接口故障分别保留。</footer></main>
<script src="flash.js?v=''' + version + '''"></script></body></html>''')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--destination', type=Path, required=True)
    build(parser.parse_args().destination)
