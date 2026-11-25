'use strict';
(() => {
  const BASE = 'https://huggingface.co/datasets/HuggingFriends/PixelWorld/resolve/main/';
  const SPACE = 'https://huggingfriends-pixelworld-dashboard.static.hf.space/';
  const NATIVE = 'gpt62-astra-native';
  const BENCHMARKS = {
    physics_iq: {label:'Physics-IQ Verified', total:198, suite:'physics_iq_720p24fps', spec:'I2V · 720p · 24 FPS · 5 s'},
    rbench: {label:'RBench Common Manipulation', total:50, suite:'rbench_common_manipulation', spec:'I2V · 720 short edge · 24 FPS · 5 s'},
    phygenbench: {label:'PhyGenBench', total:160, suite:'phygenbench_t2v_main720p24fps', spec:'T2V · 720p · 24 FPS · 5 s'},
    pcve: {label:'PCVE-RigidBench', total:129, suite:'pcve_standard720p24fps', spec:'V2V · 720p · 24 FPS · 4 s'}
  };
  const ALIASES = {'physics_iq_baseline.html':'physics_iq','rbench_common_manipulation.html':'rbench',
    'phygenbench.html':'phygenbench','phygen_pixelspace.html':'phygenbench','pcve_rigid.html':'pcve'};
  const LABELS = {[NATIVE]:'GPT-6.2-Astra', 'gpt62-sol':'GPT-6.2-Sol','gpt61-sol':'GPT-6.1-Sol',
    'gpt6-astra':'GPT-6-Astra','gpt56-sol':'GPT-5.6-Sol','gpt56-terra':'GPT-5.6-Terra',
    'gpt56-luna':'GPT-5.6-Luna','deepseek-v41':'DeepSeek-V4.1-Flash','seed21-pro':'Seed-2.1-Pro-0915','glm54':'GLM-5.4'};
  const safePath = value => typeof value === 'string' && /^[A-Za-z0-9_./-]+\.json$/.test(value) && !value.startsWith('/') && !value.split('/').includes('..');
  function benchmarkFor(value, filename='', index='') {
    if (BENCHMARKS[value]) return value;
    if (ALIASES[filename]) return ALIASES[filename];
    return Object.keys(BENCHMARKS).find(key => index.startsWith(key + '_') || index.startsWith(key + '/')) || null;
  }
  function selectGroups(catalogue, benchmark, model, suite=null, track='vision-agent', index=null) {
    if(!benchmark&&!suite&&!index)return [];
    return (catalogue.groups || []).filter(group => {
      if (!safePath(group.path) || (model && group.model_key !== model) || group.track !== track) return false;
      if (index) return group.path === index;
      if (suite) return group.suite === suite;
      if (!benchmark) return true;
      if (group.model_key === NATIVE) return group.suite?.startsWith(benchmark + '_native_astra62_');
      const expected = BENCHMARKS[benchmark].suite;
      return group.suite === expected || (benchmark === 'rbench' ? group.suite?.startsWith(expected + '_') :
        group.suite === expected + '_gpt62' || group.suite === expected + '_' + group.model_key);
    });
  }
  function assetURL(name, area='data') {
    if (typeof name !== 'string' || !name) return null;
    try {
      const url = new URL(name, BASE + area + '/');
      if (url.protocol !== 'https:' || !(url.href.startsWith(BASE) || url.href.startsWith(SPACE))) return null;
      return url.href;
    } catch (_) {return null;}
  }
  const state = row => row.status || row.state || 'pending';
  const rank = row => ['complete','finished'].includes(state(row)) ? 0 : ['running','generating','validating'].includes(state(row)) ? 1 : ['failed','exhausted','blocked'].includes(state(row)) ? 3 : 2;
  function mergeCases(indexes) {
    const joined = new Map(), seen = new Set();
    for (const index of indexes) {
      for (const [caseId, history] of Object.entries(index.cases || {})) {
        if (!Array.isArray(history)) continue;
        for (const original of history) {
          const fingerprint = original.detail || `${index.suite}:${caseId}:${original.attempt || 1}`;
          if (seen.has(fingerprint)) continue;
          seen.add(fingerprint);
          if (original.detail && !safePath(original.detail)) continue;
          const row = {...original, case:caseId, suite:index.suite};
          if (!joined.has(caseId)) joined.set(caseId, []);
          joined.get(caseId).push(row);
        }
      }
    }
    return [...joined].map(([id, history]) => {
      history.sort((a,b) => rank(a)-rank(b) || (a.hf_detail_ready===true?0:a.hf_detail_ready===false?2:1)-(b.hf_detail_ready===true?0:b.hf_detail_ready===false?2:1) || Number(!!b.reused)-Number(!!a.reused) || Number(b.attempt || 0)-Number(a.attempt || 0));
      return {id, history, current:history[0]};
    }).sort((a,b) => a.id.localeCompare(b.id, undefined, {numeric:true}));
  }
  function readUsage(data={}, row={}) {
    const usage = data.token_usage || data.usage || row.token_usage || {};
    const known = (...values) => values.find(value => typeof value === 'number' && Number.isFinite(value)) ?? null;
    return {input_tokens:known(usage.input_tokens,data.input_tokens,row.input_tokens),
      cached_input_tokens:known(usage.cached_input_tokens,data.cached_input_tokens,row.cached_input_tokens),
      output_tokens:known(usage.output_tokens,data.output_tokens,row.output_tokens),
      reasoning_output_tokens:known(usage.reasoning_output_tokens,data.reasoning_output_tokens,row.reasoning_output_tokens),
      duration_seconds:known(data.duration_seconds,row.duration_seconds)};
  }
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = {benchmarkFor,selectGroups,mergeCases,assetURL,readUsage,safePath}; return;
  }
  const query = new URLSearchParams(location.search);
  const indexPath = query.get('index');
  const benchmark = benchmarkFor(query.get('benchmark') || document.body.dataset.benchmark, location.pathname.split('/').pop(), indexPath || query.get('suite') || '');
  const spec = BENCHMARKS[benchmark];
  let model = query.get('model'), selected = query.get('case'), selectedAttempt = null;
  let cases=[], page=0, busy=false, detailSequence=0, lastSignature='', mainStatus={}, autoSelect=true;
  const byId = id => document.getElementById(id);
  const elem = (tag,text,className) => {const node=document.createElement(tag);if(text!==undefined)node.textContent=text;if(className)node.className=className;return node;};
  const number = value => typeof value === 'number' && Number.isFinite(value) ? Math.round(value).toLocaleString() : '—';
  const duration = value => typeof value === 'number' ? `${(value/60).toFixed(1)} min` : '—';
  const labels = {complete:'已交付',running:'运行中',generating:'生成中',validating:'验证中',failed:'失败',exhausted:'终止',blocked:'待恢复',pending:'待生成',waiting:'等待',finished:'完成'};
  const recordKey = row => row.detail || [row.suite,row.case,row.attempt||1].join(':');
  const badge = row => elem('span', labels[state(row)] || state(row), 'badge ' + (rank(row)===0?'complete':rank(row)===1?'running':rank(row)===3?'failed':''));
  async function fetchJSON(path, fallback=false) {
    if (!safePath(path)) throw Error('Invalid record path');
    for (const area of fallback ? ['live','data'] : ['live']) {
      try {
        const reply=await fetch(BASE+area+'/'+path+'?_='+Date.now(),{cache:'no-store',signal:AbortSignal.timeout(20000)});
        if (!reply.ok) throw Error('Waiting for publication');
        return await reply.json();
      } catch(error) {if(area==='data'||!fallback)throw error;}
    }
  }
  function route(modelKey) {
    const p=new URLSearchParams();if(benchmark)p.set('benchmark',benchmark);if(modelKey)p.set('model',modelKey);
    if(query.get('suite'))p.set('suite',query.get('suite'));if(query.get('track'))p.set('track',query.get('track'));
    return 'videos.html?'+p;
  }
  function stat(value,label) {const box=elem('div',undefined,'stat');box.append(elem('b',value),elem('span',label));return box;}
  function stats() {
    const complete=cases.filter(row=>rank(row.current)===0).length;
    const running=cases.filter(row=>rank(row.current)===1).length;
    const output=cases.filter(row=>rank(row.current)===0).map(row=>readUsage({},row.current).output_tokens).filter(value=>value!==null);
    const root=byId('stats');root.hidden=false;
    root.replaceChildren(stat(`${complete} / ${spec?.total || cases.length}`,'有效交付'),stat(number(running),'运行中'),
      stat(number(cases.length),'已有样本记录'),stat(output.length ? number(output.reduce((a,b)=>a+b,0)/output.length) : '—','Output 均值 · 有效交付'));
  }
  function renderCards() {
    const filtered=cases.filter(row=>(!byId('only-valid').checked||rank(row.current)===0)&&row.id.includes(byId('search').value.trim()));
    page=Math.max(0,Math.min(page,Math.max(0,Math.ceil(filtered.length/24)-1)));
    byId('page').textContent=`${filtered.length} 个样本 · ${page+1} / ${Math.max(1,Math.ceil(filtered.length/24))}`;
    byId('prev').disabled=page===0;byId('next').disabled=(page+1)*24>=filtered.length;
    byId('cases').replaceChildren();
    for(const item of filtered.slice(page*24,(page+1)*24)) {
      const row=item.current,card=elem('button',undefined,'case'+(item.id===selected?' selected':''));
      card.dataset.case=item.id;
      const poster=row.candidate_count!==0?assetURL(row.poster):null;
      if(poster) {const img=elem('img');img.src=poster;img.loading='lazy';img.alt=`样本 ${item.id}`;img.addEventListener('error',()=>img.replaceWith(elem('div','预览待同步','placeholder')),{once:true});card.append(img);}
      else card.append(elem('div',rank(row)===0?'预览待同步':labels[state(row)]||'待同步','placeholder'));
      const info=elem('div',undefined,'info');info.append(elem('b',item.id),badge(row),elem('div',`${item.history.length} 条运行记录 · ${number(readUsage({},row).output_tokens)} output`,'small'));
      card.append(info);card.onclick=()=>{selected=item.id;autoSelect=true;selectedAttempt=null;renderCards();showSelected(true);};byId('cases').append(card);
    }
    if(!filtered.length)byId('cases').append(elem('div','当前筛选没有样本。','empty'));
  }
  function metrics(usage) {
    const root=elem('div',undefined,'metrics');
    for(const [key,label] of [['input_tokens','Input'],['cached_input_tokens','Cached input'],['output_tokens','Output'],['duration_seconds','耗时']]) {
      const box=elem('div');box.append(elem('b',key==='duration_seconds'?duration(usage[key]):number(usage[key])),elem('span',label));root.append(box);
    }
    return root;
  }
  async function showSelected(force=false) {
    const item=cases.find(value=>value.id===selected);if(!item)return;
    const row=item.history.find(value=>recordKey(value)===selectedAttempt)||item.current;
    const signature=JSON.stringify([selected,recordKey(row),row.status,row.hf_detail_ready,row.detail_sha256,row.poster,row.output_tokens,row.candidate_count]);
    const playing=byId('detail').querySelector('video');
    if(!force&&(signature===lastSignature||(playing&&!playing.paused)))return;
    lastSignature=signature;const sequence=++detailSequence,target=byId('detail');target.hidden=false;target.className='detail panel';
    const heading=elem('div',undefined,'detail-head');heading.append(elem('h2',`样本 ${item.id}`));
    const select=elem('select');select.setAttribute('aria-label','运行记录');
    for(const [i,attempt] of item.history.entries()){const option=elem('option',`Attempt ${attempt.attempt || 1} · ${labels[state(attempt)]||state(attempt)}${attempt.reused?' · 历史复用':''}`);option.value=String(i);option.selected=attempt===row;select.append(option);}
    select.onchange=()=>{selectedAttempt=recordKey(item.history[Number(select.value)]);showSelected(true);};
    const actions=elem('div',undefined,'controls'),close=elem('button','关闭详情');close.onclick=()=>{selected=null;autoSelect=false;lastSignature='';detailSequence++;target.hidden=true;renderCards();};actions.append(select,close);heading.append(actions);
    target.replaceChildren(heading,badge(row),metrics(readUsage({},row)));
    if(!row.detail||row.hf_detail_ready===false) {target.append(elem('p','运行状态已登记，视频与完整 Trace 正在同步。','notice'));return;}
    try {
      const data=await fetchJSON(row.detail,true);if(sequence!==detailSequence)return;
      target.querySelector('.metrics').replaceWith(metrics(readUsage(data,row)));
      if(data.prompt)target.append(elem('p',data.prompt));
      const media=elem('div',undefined,'media-grid');
      for(const [i,candidate] of (data.candidates||[]).entries()) {
        const url=assetURL(candidate.preview_mp4);if(!url)continue;
        const figure=elem('figure'),video=elem('video');video.controls=true;video.preload='none';video.src=url;
        const poster=assetURL(candidate.poster);if(poster)video.poster=poster;
        figure.append(video,elem('figcaption',candidate.label||`候选 ${i+1}`));media.append(figure);
      }
      if(media.children.length)target.append(media);else target.append(elem('p','这条运行记录没有可播放的已发布候选。','notice'));
      if(data.candidate_history_pending)target.append(elem('p','候选历史仍在同步'+(media.children.length?'；当前已发布的视频可以播放。':'。'),'small'));
      const firstFrame=assetURL(data.first_frame),sourceVideo=assetURL(data.source_video);
      if(firstFrame||sourceVideo){const inputs=elem('details'),grid=elem('div',undefined,'media-grid');inputs.append(elem('summary','输入首帧 / 源视频'));
        if(firstFrame){const figure=elem('figure'),img=elem('img');img.src=firstFrame;img.alt='输入首帧';img.loading='lazy';figure.append(img,elem('figcaption','输入首帧'));grid.append(figure);}
        if(sourceVideo){const figure=elem('figure'),video=elem('video');video.controls=true;video.preload='none';video.src=sourceVideo;figure.append(video,elem('figcaption','输入源视频'));grid.append(figure);}
        inputs.append(grid);target.append(inputs);}
      if(data.validation||data.diagnostics){const diagnostics=elem('details');diagnostics.append(elem('summary','交付校验 / 诊断'),elem('pre',JSON.stringify({validation:data.validation,diagnostics:data.diagnostics},null,2)));target.append(diagnostics);}
      const links=elem('div',undefined,'links'),trace=assetURL(data.trace_archive,'live');
      if(trace){const a=elem('a','下载完整 Trace');a.href=trace;links.append(a);}
      const record=elem('a','运行记录 JSON');record.href=BASE+'live/'+row.detail;links.append(record);target.append(links);
      const candidates=data.candidates||[];
      const code=data.generate_py||data.code||data.generator||[...candidates].reverse().find(value=>value.code_text||value.code)?.code_text||[...candidates].reverse().find(value=>value.code)?.code;
      if(code){const section=elem('details');section.append(elem('summary','生成代码'),elem('pre',typeof code==='string'?code:JSON.stringify(code,null,2)));target.append(section);}
      if((data.events||[]).length){const section=elem('details');section.append(elem('summary',`工具与修订记录 · ${data.events.length} 条`));
        for(const event of data.events.slice(0,100)){const entry=elem('div',undefined,'event');entry.append(elem('b',event.kind||event.type||'工具事件'));
          const parts=[event.command,event.aggregated_output,event.text||event.message||event.code].filter(value=>value!==undefined&&value!==null&&value!=='');
          for(const part of parts.length?parts:[event])entry.append(elem('pre',typeof part==='string'?part:JSON.stringify(part,null,2)));
          for(const name of event.images||[]){const url=assetURL(name);if(url){const img=elem('img');img.src=url;img.loading='lazy';entry.append(img);}}section.append(entry);}
        target.append(section);}
    } catch(error){lastSignature='';target.append(elem('p','这条详情正在同步，页面将自动重试。','notice'));}
  }
  function nativeFallback(indexes) {
    if(model!==NATIVE||!benchmark)return indexes;
    const smoke=mainStatus.native_main_smoke||{},pending=[];
    for(const row of smoke.samples||[])pending.push({...row,suite:benchmark+'_native_astra62_smoke'+(row.round===2?'_round2':'')});
    for(const row of smoke.additional_samples||[])pending.push({...row,suite:`${row.benchmark}_native_astra62_extra_${row.case}`});
    for(const row of smoke.quota_study?.samples||[])pending.push({...row,suite:row.benchmark+'_native_astra62_quota5'});
    for(const row of pending.filter(value=>value.benchmark===benchmark&&['generating','running','validating','complete','failed','finished'].includes(value.state))) {
      if(query.get('suite')&&row.suite!==query.get('suite'))continue;
      if(indexes.some(index=>index.suite===row.suite&&index.cases?.[row.case]))continue;
      indexes.push({suite:row.suite,cases:{[row.case]:[{...row,status:row.state,attempt:1,hf_detail_ready:false}]}});
    }
    return indexes;
  }
  async function poll() {
    if(!benchmark&&!indexPath){
      byId('title').textContent='选择基准';byId('subtitle').textContent='各基准的模型与样本独立展示';
      byId('models').hidden=false;byId('models').replaceChildren();byId('toolbar').hidden=true;byId('stats').hidden=true;
      for(const [key,value] of Object.entries(BENCHMARKS)){const card=elem('a',undefined,'model-card');
        const params=new URLSearchParams({benchmark:key});if(model)params.set('model',model);card.href='videos.html?'+params;
        card.append(elem('b',value.label),elem('span',`${value.total} 个官方样本 · ${value.spec}`,'small'));byId('models').append(card);}
      byId('sync').textContent='选择基准后查看实时 HF 运行记录。';return;
    }
    if(busy)return;busy=true;
    try {
      if(indexPath&&!safePath(indexPath))throw Error('Invalid index');
      const catalogue=await fetchJSON('catalogue.json');
      if(!model&&indexPath)model=indexPath.split('/')[1];
      if(model===NATIVE){try{const reply=await fetch(BASE+'metadata/main_suite_status.json?_='+Date.now(),{cache:'no-store'});if(reply.ok)mainStatus=await reply.json();}catch(_) {}}
      const groups=selectGroups(catalogue,benchmark,model,query.get('suite'),query.get('track')||'vision-agent',indexPath);
      const allGroups=selectGroups(catalogue,benchmark,null,query.get('suite'),query.get('track')||'vision-agent');
      const keys=[...new Set(allGroups.map(group=>group.model_key))];if(model&&!keys.includes(model))keys.unshift(model);
      byId('model-select').replaceChildren();for(const key of keys){const option=elem('option',LABELS[key]||key);option.value=key;option.selected=key===model;byId('model-select').append(option);}
      byId('title').textContent=model?`${LABELS[model]||model} · ${spec?.label||'实验详情'}`:spec?.label||'实验记录';
      byId('subtitle').textContent=(spec?.spec||'')+(model===NATIVE?' · Codex 0.172.0 · high · default':'');
      byId('back').href=model?route(null):'index.html';byId('back').textContent=model?'← 基准与模型':'← 主实验';
      if(!model){byId('models').hidden=false;byId('models').replaceChildren();byId('cases').replaceChildren();
        for(const key of keys){const card=elem('a',undefined,'model-card');card.href=route(key);card.append(elem('b',LABELS[key]||key),elem('span',key===NATIVE?'原生 Codex 0.172.0 · 查看全部运行':'查看样本与运行记录','small'));byId('models').append(card);}
        if(!keys.length)byId('models').append(elem('div','实验记录正在同步。','empty'));
      } else {
        byId('models').hidden=true;byId('toolbar').hidden=false;
        const settled=await Promise.allSettled(groups.map(group=>fetchJSON(group.path,true)));
        let indexes=settled.filter(result=>result.status==='fulfilled').map(result=>result.value);
        if(indexPath&&!groups.length){try{indexes=[await fetchJSON(indexPath,true)];}catch(_) {}}
        indexes=nativeFallback(indexes);
        if(!indexes.length&&cases.length)throw Error('Waiting for indices');
        cases=mergeCases(indexes);stats();renderCards();
        if(!selected&&autoSelect&&cases.length)selected=cases[0].id;
        if(selected&&!cases.some(item=>item.id===selected))selected=cases[0]?.id;
        renderCards();await showSelected();
      }
      byId('sync').textContent=`HF 更新：${catalogue.updated_utc||'未知'} · 每 60 秒刷新`;
    } catch(error){byId('sync').textContent='等待 HF 同步；已有样本保留，稍后重试。';}
    finally{busy=false;}
  }
  byId('model-select').onchange=()=>{location.href=route(byId('model-select').value);};
  byId('only-valid').onchange=byId('search').oninput=()=>{page=0;renderCards();};
  byId('prev').onclick=()=>{page--;renderCards();};byId('next').onclick=()=>{page++;renderCards();};
  poll();setInterval(poll,60000);document.addEventListener('visibilitychange',()=>{if(!document.hidden)poll();});
})();
