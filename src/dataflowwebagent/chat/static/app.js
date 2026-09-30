const $ = id => document.getElementById(id);
const state = {token: '', sid: null, selectedRun: null, config: {}, snapshot: null, messageKey: '', generation: 0, sourceChoiceId: null, sourceCandidateIds: [], sourceSelections: []};
const labels = {queued:'等待执行',running:'进行中',completed:'已完成',failed:'失败',cancelled:'已停止',interrupted:'已中断',awaiting_source_selection:'等待选择来源',needs_confirmation:'数量不足 · 待确认',accepted_partial:'已接受当前数量',continued:'已创建补充版本'};
const reasons = {dataset_exhausted:'已处理本次找到的数据集样本',no_suitable_dataset:'没有找到适合的数据集',existing_sources_exhausted:'已有资料已处理完毕'};
const stages = {'searching / collecting sources':'正在寻找数据集来源','searching dataset catalogs':'正在搜索数据集站点','processing dataset records':'正在处理数据集样本','merging and deduplicating collected sources':'正在合并并去除重复资料','extracting text':'提取正文','checking relevance':'判断相关性','filtering sources':'筛选资料','generating QA':'生成问答','validating QA':'检查问答格式','exporting QA':'导出数据'};
const number = n => Number(n || 0).toLocaleString('en-US');
function datasetSize(value){const bytes=Number(value);if(!Number.isFinite(bytes)||bytes<=0)return '大小未标注';const units=['B','KB','MB','GB','TB'];let amount=bytes,index=0;while(amount>=1024&&index<units.length-1){amount/=1024;index++;}return `${amount>=10||index===0?Math.round(amount):amount.toFixed(1)} ${units[index]}`;}
function el(tag, cls, text) {const node=document.createElement(tag);if(cls)node.className=cls;if(text!==undefined)node.textContent=text;return node;}
function notice(message='') {$('notice').textContent=message;$('notice').hidden=!message;}
async function api(path, data) {
  const options = data === undefined ? {} : {method:'POST',headers:{'Content-Type':'application/json','X-DataVoyager-Token':state.token},body:JSON.stringify(data)};
  const response=await fetch(path, options);
  const body=await response.json();
  if(!response.ok) throw new Error(body.error || '请求失败');
  return body;
}
function configView(config){state.config=config;$('api-label').textContent=config.configured?'API 已配置':'连接模型 API';$('api-dot').classList.toggle('connected',config.configured);}
function sessionsView(sessions){const nodes=sessions.map(s=>{const b=el('button','chat-link'+(s.id===state.sid?' selected':''),s.title);b.title=s.title;b.onclick=()=>selectSession(s.id);return b;});$('sessions').replaceChildren(...nodes);}
async function selectSession(id){state.sid=id;state.selectedRun=null;state.snapshot=null;state.messageKey='';state.generation++;await refresh();}
async function createSession(){const session=await api('/api/sessions',{});await selectSession(session.id);$('prompt').focus();}
function render(snapshot){
  const previous=state.snapshot;state.snapshot=snapshot;
  $('chat-title').textContent=snapshot.title==='新数据集'?'从一个想法开始':snapshot.title;
  $('welcome').hidden=snapshot.messages.length>0;
  const key=JSON.stringify(snapshot.messages);
  if(key!==state.messageKey){
    const wasNearBottom=$('feed').scrollHeight-$('feed').scrollTop-$('feed').clientHeight<120;
    const nodes=snapshot.messages.map(m=>{const node=el('article','message '+m.role);node.append(el('div','speaker',m.role==='user'?'你':m.role==='system'?'工作台':'DataVoyager'),el('div','body',m.content));return node;});
    $('messages').replaceChildren(...nodes);state.messageKey=key;
    if(wasNearBottom || !previous) $('feed').scrollTop=$('feed').scrollHeight;
  }
  $('thinking').hidden=!snapshot.busy;$('send').disabled=snapshot.busy;
  const plan=snapshot.pending_plan;$('plan-card').hidden=!plan;
  if(plan){
    const revising=plan.decision.action==='revise';
    $('plan-budget').parentElement.hidden=revising;
    $('plan-explanation').textContent=revising?`要求 ${number(plan.target_rows)} 条，复用现有资料最多支持 ${number(plan.capacity_upper_bound)} 条，实际可能更少。可以降低目标，或在对话中提出新增采集。`:`要求 ${number(plan.target_rows)} 条。按每份资料最多六条候选计算，理论上限为 ${number(plan.capacity_upper_bound)} 条，实际可能更少。尚未开始采集。`;
    if(state.planId!==plan.id){state.planId=plan.id;$('plan-target').value=plan.target_rows;$('plan-budget').value=plan.max_source_rows ?? plan.max_pages;}
    $('confirm-plan').disabled=snapshot.busy;$('dismiss-plan').disabled=snapshot.busy;
  }
  if(!state.selectedRun || previous?.runs.length!==snapshot.runs.length) state.selectedRun=snapshot.runs.at(-1)?.id || null;
  const options=snapshot.runs.length?snapshot.runs.map(r=>{const opt=el('option','',`v${r.version} · ${r.action==='revise'?'复用资料改写':r.action==='extend'?'保留题目补采':'数据集来源'} · ${labels[r.status] || r.status}`);opt.value=r.id;return opt;}):[Object.assign(el('option','','还没有生成记录'),{value:''})];
  $('versions').replaceChildren(...options);$('versions').value=state.selectedRun || '';
  renderRun();
}
function renderRun(){
  const snapshot=state.snapshot;if(!snapshot)return;
  const run=snapshot.runs.find(r=>r.id===state.selectedRun);
  const p=run?.progress || {}, u=run?.report?.usage || p.usage || {};
  $('run-status').textContent=run?(labels[run.status] || run.status):'等待开始';
  $('run-status').className='status-pill'+(run?.status==='running'?' active':'')+(['failed','interrupted'].includes(run?.status)?' failed':'');
  const translated=(p.stage || '').split(', ').map(s=>stages[s] || labels[s] || s).join(' · ');
  $('run-detail').textContent=run?.error || (run ? `${translated || labels[run.status]}${run.action==='revise'?' · 复用已有资料':''}${run.base_run_id?' · 原版本保留':''}${p.elapsed_seconds!==undefined?' · '+Math.round(p.elapsed_seconds)+'s':''}`:'从左边发起一个需求，进度与样例会出现在这里。');
  const candidates=run?.report?.selection_candidates||[];
  $('source-choice-card').hidden=run?.status!=='awaiting_source_selection';
  if(run?.status==='awaiting_source_selection'){
    const candidateIds=candidates.map(candidate=>candidate.dataset_id).filter(Boolean);
    // Polling redraws this panel every two seconds.  Keep the selection as
    // state belonging to this exact discovery result instead of depending on
    // whichever checkbox nodes happened to exist when the button was clicked.
    if(state.sourceChoiceId!==run.id){state.sourceChoiceId=run.id;state.sourceCandidateIds=candidateIds;state.sourceSelections=candidateIds.length?[candidateIds[0]]:[];}
    else {state.sourceCandidateIds=candidateIds;state.sourceSelections=state.sourceSelections.filter(id=>candidateIds.includes(id));}
    const cards=candidates.map((candidate,index)=>{
      const label=el('label','source-candidate');const check=document.createElement('input');check.type='checkbox';check.className='source-pick';check.value=candidate.dataset_id;check.checked=state.sourceSelections.includes(candidate.dataset_id);check.onchange=()=>{const id=candidate.dataset_id;state.sourceSelections=check.checked?[...new Set([...state.sourceSelections,id])]:state.sourceSelections.filter(selected=>selected!==id);};
      const body=el('span','source-candidate-body');body.append(el('strong','',candidate.title||candidate.dataset_id),el('small','',`${candidate.source==='kaggle'?'Kaggle':'Hugging Face'} · ${candidate.dataset_id} · ${candidate.language||'语言未标注'} · ${candidate.rows_estimate?number(candidate.rows_estimate)+' 条':''} · 许可：${candidate.license||'unknown'} · ${candidate.curated?'已核对数据卡':''}`),el('span','',candidate.description||'暂无数据集说明。'));
      body.append(el('span','source-kind',`数据规模：${candidate.size_category||'未标注'} · 仓库大小：${datasetSize(candidate.size)}${candidate.quick_trial?' · 适合快速试跑':''}`));
      if(candidate.data_kind)body.append(el('span','source-kind',`数据类型：${candidate.data_kind}${candidate.schema_summary?' · 字段：'+candidate.schema_summary:''}`));
      if(candidate.curator_note)body.append(el('span','source-curator-note',candidate.curator_note));
      try{const url=new URL(candidate.url);if(['https:','http:'].includes(url.protocol)){const a=el('a','',url.hostname+' ↗');a.href=url.href;a.target='_blank';a.rel='noopener noreferrer';body.append(a);}}catch{}
      label.append(check,body);return label;
    });$('source-candidates').replaceChildren(...cards);
  }
  $('selection-target-wrap').hidden=$('selected-stage').value!=='qa';
  $('select-sources').disabled=snapshot.busy||run?.status!=='awaiting_source_selection';
  $('pages').textContent=run?number(p.source_rows ?? p.pages_collected):'—';$('sources').textContent=run?number(p.sources_accepted):'—';$('qas').textContent=run?number(p.qa_candidates):'—';
  const acquisition=p.source_acquisition || run?.report?.source_acquisition || {}, hf=acquisition.datasets || acquisition.huggingface || {};
  $('source-progress').textContent=!run?'':hf.status==='searching'?'正在 Hugging Face 等数据集站点搜索来源。':hf.selected_dataset_id?`${hf.source==='kaggle'?'Kaggle':'Hugging Face'} · ${hf.selected_dataset_id} · 下载 ${number(hf.records_loaded)} 条${hf.license&&hf.license!=='unknown'?' · '+hf.license:''}。`:hf.error?`数据集搜索或下载暂不可用：${hf.error}`:hf.status==='no_suitable_dataset'?'找到的数据集不符合本次需求；可调整领域关键词或数据来源要求。':hf.status==='no_results'?'在当前可用的数据集站点没有找到匹配数据集。':hf.status==='download_failed'?'数据集下载失败；可以调整需求后重新搜索。':'正在准备数据集来源信息。';
  const target=run?.report?.target_rows || run?.target_rows;
  const generated=run?.report?.rows ?? p.generated_rows ?? 0;
  $('quantity-progress').hidden=!target;
  $('quantity-progress').textContent=`已去重 ${number(generated)} / 目标 ${number(target)} 条${p.round?' · 第 '+p.round+' 轮':''}。题数不代表事实质量已验证。`;
  const shortfall=run?.status==='needs_confirmation';$('shortfall-card').hidden=!shortfall;
  if(shortfall){
    $('shortfall-explanation').textContent=`${reasons[run.report.stop_reason] || '本次采集已结束'}。已有 ${number(generated)} 条，还差 ${number(run.report.shortfall)} 条。`;
    $('accept-shortfall').textContent=`接受当前 ${number(generated)} 条`;
    $('accept-shortfall').disabled=snapshot.busy||!generated;
    $('extend-shortfall').disabled=snapshot.busy||snapshot.runs.some(r=>['queued','running'].includes(r.status));
    if(state.shortfallId!==run.id){state.shortfallId=run.id;$('extra-pages').value=Math.min(1000,Math.max(50,run.report.shortfall));}
  }
  const turns=snapshot.agent_turns || [];const input=turns.reduce((s,t)=>s+(t.usage?.input_tokens || 0),0),output=turns.reduce((s,t)=>s+(t.usage?.output_tokens || 0),0);
  $('agent-turns').textContent=number(turns.length);$('agent-tokens').textContent=`${number(input)} / ${number(output)}`;
  $('api-calls').textContent=`${number(u.calls)}${u.failed_calls?' · '+u.failed_calls+' 次失败':''}${u.in_flight?' · '+u.in_flight+' 个处理中':''}`;
  $('qa-tokens').textContent=`${number(u.input_tokens)} / ${number(u.output_tokens)}`;
  const partial=(run&&u.usage_complete===false)||turns.some(t=>!t.usage);
  $('usage-note').textContent=partial?'部分用量尚未返回或不可用；显示已报告 token，不推算金额。':'用量来自接口返回；主 Agent 统计对话轮次，流水线统计请求次数，不推算金额。';
  const stageNames={'raw':'找到的原始数据','merged':'合并去重数据','corpus':'清洗后正文','source-review':'正文筛选记录','candidates':'QA 候选及审核'};
  $('stage-downloads').replaceChildren(...Object.entries(stageNames).map(([key,label])=>{
    const count=run?.stage_artifacts?.[key]||0;
    const node=el(count?'a':'span','stage-download',`${label} · ${number(count)} 条${count?' ↓':''}`);
    if(count)node.href=`/api/sessions/${state.sid}/runs/${run.id}/download/${key}`;
    return node;
  }));
  const reviews=run?.review_counts||{};
  $('review-counts').textContent=run?`来源审核通过 ${number(reviews.source_supported)} · 待复核 ${number(reviews.needs_review)} · 尚未审核 ${number(reviews.unreviewed)} · 重复 ${number(reviews.duplicate)}`:'';
  $('quality-note').textContent=(run?.report?.factual_verification==='model_source_review'||run?.stop_after==='qa')?'目标题数仅统计来源审核通过并去重的 QA；模型审核不等于专家认证。':run&&['raw','corpus','collect','merge','clean'].includes(run.stop_after)?'这个版本按选定阶段停止，后续可从已导出的数据继续处理。':'旧版本或尚未完成审核的样本，不计为已验证事实。';
  const samples=run?.samples || [];
  $('sample-count').textContent=run?.report?.rows!==undefined?`共 ${number(run.report.rows)} 条 · 预览 ${samples.length} 条`:`${samples.length} 条候选 · 最多 5 条`;
  if(samples.length){$('samples').replaceChildren(...samples.map((s,i)=>{const card=el('article','sample');card.append(el('span','sample-index',`QA / ${String(i+1).padStart(2,'0')}`),el('h4','',s.instruction),el('p','',s.output));try{const url=new URL(s.source_url);if(['https:','http:'].includes(url.protocol)){const a=el('a','',url.hostname+' ↗');a.href=url.href;a.target='_blank';a.rel='noopener noreferrer';card.append(a);}}catch{}return card;}));}
  else{const empty=el('div','empty-samples');empty.append(el('span','','▤'),el('p','',run?'暂时还没有可展示的问答。':'第一条问答，正在等你的想法。'),el('small','','生成后可在对话中提出修改意见'));$('samples').replaceChildren(empty);}
  $('run-actions').hidden=!run;$('cancel-run').hidden=!run||!['queued','running'].includes(run.status);
  for(const [id,name] of [['download-qa','qa'],['download-sources','sources']]){const a=$(id);a.hidden=!run?.downloadable;if(run)a.href=`/api/sessions/${state.sid}/runs/${run.id}/download/${name}`;}
}
async function refresh(){
  const sid=state.sid,generation=state.generation;
  try{const [sessions,snapshot]=await Promise.all([api('/api/sessions'),sid?api('/api/sessions/'+sid):Promise.resolve(null)]);if(generation!==state.generation)return;sessionsView(sessions);if(snapshot)render(snapshot);}
  catch(error){notice('连接未更新：'+error.message);}
}
function openSettings(){
  $('base-url').value=state.config.base_url || '';$('model').value=state.config.model || '';$('api-format').value=state.config.api_format || 'responses';$('api-key').value='';$('api-key').placeholder=state.config.configured?'已配置；留空保留当前 Key':'仅保存在服务进程内存中';$('settings-error').textContent='';$('settings').showModal();
}
$('settings-button').onclick=openSettings;$('close-settings').onclick=()=>$('settings').close();
$('settings-form').onsubmit=async event=>{event.preventDefault();try{const apiKey=$('api-key').value;const config=await api('/api/config',{base_url:$('base-url').value,model:$('model').value,api_key:apiKey,api_format:$('api-format').value});if(apiKey)sessionStorage.setItem('datavoyager.api_key',apiKey);configView(config);$('api-key').value='';$('settings').close();notice();$('prompt').focus();}catch(error){$('settings-error').textContent=error.message;}};
$('new-chat').onclick=()=>createSession().catch(error=>notice(error.message));
$('versions').onchange=()=>{state.selectedRun=$('versions').value;renderRun();};
$('composer').onsubmit=async event=>{event.preventDefault();const message=$('prompt').value.trim(),stopAfter=$('stop-after').value;if(!message||state.snapshot?.busy)return;if(!state.config.configured&&stopAfter!=='discover'){openSettings();return;}$('send').disabled=true;try{if(!state.sid)await createSession();await api(`/api/sessions/${state.sid}/messages`,{message,stop_after:stopAfter});$('prompt').value='';notice();await refresh();}catch(error){notice(error.message);$('send').disabled=false;}};
$('prompt').onkeydown=event=>{if(event.key==='Enter'&&!event.shiftKey&&!event.isComposing){event.preventDefault();$('composer').requestSubmit();}};
for(const button of document.querySelectorAll('[data-prompt]'))button.onclick=()=>{$('prompt').value=button.dataset.prompt;$('prompt').focus();};
$('cancel-run').onclick=async()=>{try{await api(`/api/sessions/${state.sid}/runs/${state.selectedRun}/cancel`,{});await refresh();}catch(error){notice(error.message);}};
async function confirmPlan(action){try{await api(`/api/sessions/${state.sid}/confirm`,{plan_id:state.snapshot.pending_plan.id,action,target_rows:Number($('plan-target').value),max_source_rows:Number($('plan-budget').value)});notice();await refresh();}catch(error){notice(error.message);}}
$('confirm-plan').onclick=()=>confirmPlan('start');$('dismiss-plan').onclick=()=>confirmPlan('cancel');
async function resolveShortfall(action){try{await api(`/api/sessions/${state.sid}/runs/${state.selectedRun}/resolve`,{action,max_source_rows:Number($('extra-pages').value)});notice();await refresh();}catch(error){notice(error.message);}}
$('accept-shortfall').onclick=()=>resolveShortfall('accept');$('extend-shortfall').onclick=()=>resolveShortfall('extend');
$('selected-stage').onchange=()=>{$('selection-target-wrap').hidden=$('selected-stage').value!=='qa';};
$('select-sources').onclick=async()=>{try{const run=state.snapshot?.runs.find(item=>item.id===state.selectedRun);const allowed=new Set(run?.report?.selection_candidates?.map(candidate=>candidate.dataset_id).filter(Boolean)||[]);const dataset_ids=state.sourceSelections.filter(id=>allowed.has(id));if(!dataset_ids.length)throw new Error('至少选择一个数据集来源');await api(`/api/sessions/${state.sid}/runs/${run.id}/select`,{dataset_ids,stop_after:$('selected-stage').value,target_rows:Number($('selection-target').value),max_source_rows:Number($('selection-budget').value)});notice();await refresh();}catch(error){notice(error.message);}};
$('adjust-scope').onclick=()=>{$('prompt').value='当前题数不足，我想调整主题或来源范围：';$('prompt').focus();};
async function poll(){await refresh();setTimeout(poll,2000);}
(async()=>{try{const data=await api('/api/bootstrap');state.token=data.token;configView(data.config);const savedKey=sessionStorage.getItem('datavoyager.api_key');if(!data.config.configured&&savedKey&&data.config.base_url&&data.config.model){try{configView(await api('/api/config',{base_url:data.config.base_url,model:data.config.model,api_key:savedKey,api_format:data.config.api_format||'responses'}));}catch(error){sessionStorage.removeItem('datavoyager.api_key');notice('模型配置未恢复：'+error.message);}}if(data.sessions.length)await selectSession(data.sessions[0].id);else await createSession();setTimeout(poll,2000);}catch(error){notice('无法连接工作空间：'+error.message);}})();
