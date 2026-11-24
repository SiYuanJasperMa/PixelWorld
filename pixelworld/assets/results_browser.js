'use strict';
(() => {
  const base = 'https://huggingface.co/datasets/HuggingFriends/PixelWorld/resolve/main/';
  const path = new URLSearchParams(location.search).get('index') || '';
  const safePath = value => typeof value === 'string' && /^[A-Za-z0-9_./-]+\.json$/.test(value) && !value.startsWith('/') && !value.split('/').includes('..');
  if (!safePath(path)) { document.getElementById('sync').textContent = '请从主表打开对应模型。'; return; }
  const byId = id => document.getElementById(id);
  const elem = (tag, text) => { const node = document.createElement(tag); if (text !== undefined) node.textContent = text; return node; };
  let rows = [], page = 0, busy = false, active = null, activePending = false;
  const allowedURL = url => url.startsWith(base) || url.startsWith('https://huggingfriends-pixelworld-dashboard.static.hf.space/');
  const mediaURL = name => {
    if (!name) return null;
    const url = new URL(name, base + 'data/').href;
    return allowedURL(url) ? url : null;
  };
  async function loadJSON(relative) {
    if (!safePath(relative)) throw new Error('invalid path');
    const reply = await fetch(base + 'live/' + relative + '?_=' + Date.now(), {cache:'no-store'});
    if (!reply.ok) throw new Error('HF资源尚未同步');
    return reply.json();
  }
  function list() {
    const filtered = rows.filter(row => !byId('only-valid').checked || row.status === 'complete');
    page = Math.max(0, Math.min(page, Math.max(0, Math.ceil(filtered.length / 50) - 1)));
    byId('page').textContent = `${filtered.length} 条记录 · 第 ${page+1} 页`;
    byId('cases').replaceChildren();
    for (const row of filtered.slice(page*50,page*50+50)) {
      const button = elem('button', `${row.case} · ${row.attempt} · ${row.status}${row.hf_detail_ready ? '' : ' · 上传中'}`);
      button.addEventListener('click', () => show(row)); byId('cases').append(button);
    }
  }
  async function show(row) {
    active = row.detail; activePending = !row.hf_detail_ready; const target = byId('detail'); target.replaceChildren(elem('h2', `${row.case} · Attempt ${row.attempt}`));
    if (!row.hf_detail_ready) {target.append(elem('p','生成记录已登记；视频与 Trace 正在上传。页面会自动更新。'));return;}
    try {
      const data = await loadJSON(row.detail); if (active !== row.detail) return;
      target.append(elem('p', data.prompt || data.task || ''));
      for (const candidate of data.candidates || []) {
        const url = mediaURL(candidate.preview_mp4);
        if (url) {const video=elem('video');video.controls=true;video.preload='none';video.src=url;const poster=mediaURL(candidate.poster);if(poster)video.poster=poster;target.append(video);}
      }
      if (!(data.candidates || []).length) target.append(elem('p','此记录没有可播放的视频。'));
      const usage = data.usage || data.token_usage || {input_tokens:data.input_tokens, cached_input_tokens:data.cached_input_tokens, output_tokens:data.output_tokens, reasoning_output_tokens:data.reasoning_output_tokens, duration_seconds:data.duration_seconds};
      target.append(elem('pre', JSON.stringify(usage,null,2)));
      if (data.trace_archive && allowedURL(data.trace_archive)) {const link=elem('a','下载完整脱敏 Trace');link.href=data.trace_archive;target.append(link);}
      const source=elem('a',' · 查看本次运行记录');source.href=base+'live/'+row.detail;target.append(source);
      const code=data.generate_py || data.code || data.generator || (data.candidates || []).find(candidate => candidate.code_text)?.code_text;
      if (!code && data.trace_archive) target.append(elem('p','生成代码、代码修订和工具记录包含在完整 Trace 中。'));
      if (code) {const details=elem('details');details.append(elem('summary','生成代码'),elem('pre',typeof code==='string'?code:JSON.stringify(code,null,2)));target.append(details);}
    } catch (error) {target.append(elem('p','HF资源正在同步，稍后自动刷新。'));}
  }
  async function poll() {
    if (busy) return;busy=true;
    try {
      const index=await loadJSON(path);rows=[];
      for (const [id,history] of Object.entries(index.cases || {})) for(const row of history) rows.push({...row,case:id});
      rows.sort((a,b)=>a.case.localeCompare(b.case)||a.attempt-b.attempt);
      byId('title').textContent=`${index.model_key || ''} · ${index.suite || ''}`;
      byId('sync').textContent=`HF 数据更新：${index.updated_utc || index.generation_updated_utc || '未知'} · 每 60 秒同步`;
      list();
      if (activePending) {const ready = rows.find(row => row.detail === active && row.hf_detail_ready); if (ready) await show(ready);}
    } catch(error) {byId('sync').textContent='等待 HF 同步；已有记录会保留，稍后重试。';}
    finally{busy=false;}
  }
  byId('only-valid').addEventListener('change',()=>{page=0;list();});
  byId('prev').addEventListener('click',()=>{page--;list();});byId('next').addEventListener('click',()=>{page++;list();});
  poll();setInterval(poll,60000);
})();
