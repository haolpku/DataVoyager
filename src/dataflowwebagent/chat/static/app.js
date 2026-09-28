const $ = id => document.getElementById(id);
const state = {token: '', sid: null, selectedRun: null, config: {}, snapshot: null, messageKey: '', generation: 0};
const labels = {queued:'等待执行',running:'进行中',completed:'已完成',failed:'失败',cancelled:'已停止',interrupted:'已中断',needs_confirmation:'数量不足 · 待确认',accepted_partial:'已接受当前数量',continued:'已创建补充版本'};
const reasons = {page_budget_exhausted:'本次采集预算内的尝试已结束',no_new_questions:'连续两轮没有新增不同问题',round_limit:'已达到本次补采轮数上限',existing_sources_exhausted:'已有资料已处理完毕'};
const stages = {'searching / collecting sources':'正在寻找和采集资料','extracting text':'提取正文','checking relevance':'判断相关性','filtering sources':'筛选资料','generating QA':'生成问答','validating QA':'检查问答格式','exporting QA':'导出数据'};
const number = n => Number(n || 0).toLocaleString('en-US');
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
    $('plan-explanation').textContent=revising?`要求 ${number(plan.target_rows)} 条，复用现有资料最多支持 ${number(plan.capacity_upper_bound)} 条，实际可能更少。可以降低目标，或在对话中提出新增采集。`:`要求 ${number(plan.target_rows)} 条。按当前预算和每份资料最多一题的流程，上限为 ${number(plan.capacity_upper_bound)} 条，实际可能更少。尚未开始采集。`;
    if(state.planId!==plan.id){state.planId=plan.id;$('plan-target').value=plan.target_rows;$('plan-budget').value=plan.max_pages;}
    $('confirm-plan').disabled=snapshot.busy;$('dismiss-plan').disabled=snapshot.busy;
  }
  if(!state.selectedRun || previous?.runs.length!==snapshot.runs.length) state.selectedRun=snapshot.runs.at(-1)?.id || null;
  const options=snapshot.runs.length?snapshot.runs.map(r=>{const opt=el('option','',`v${r.version} · ${r.action==='revise'?'复用资料改写':r.action==='extend'?'保留题目补采':'网络采集'} · ${labels[r.status] || r.status}`);opt.value=r.id;return opt;}):[Object.assign(el('option','','还没有生成记录'),{value:''})];
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
  $('pages').textContent=run?number(p.pages_collected):'—';$('sources').textContent=run?number(p.sources_accepted):'—';$('qas').textContent=run?number(p.qa_candidates):'—';
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
    if(state.shortfallId!==run.id){state.shortfallId=run.id;$('extra-pages').value=Math.min(1000,Math.max(20,run.report.shortfall));}
  }
  const turns=snapshot.agent_turns || [];const input=turns.reduce((s,t)=>s+(t.usage?.input_tokens || 0),0),output=turns.reduce((s,t)=>s+(t.usage?.output_tokens || 0),0);
  $('agent-turns').textContent=number(turns.length);$('agent-tokens').textContent=`${number(input)} / ${number(output)}`;
  $('api-calls').textContent=`${number(u.calls)}${u.failed_calls?' · '+u.failed_calls+' 次失败':''}${u.in_flight?' · '+u.in_flight+' 个处理中':''}`;
  $('qa-tokens').textContent=`${number(u.input_tokens)} / ${number(u.output_tokens)}`;
  const partial=(run&&u.usage_complete===false)||turns.some(t=>!t.usage);
  $('usage-note').textContent=partial?'部分用量尚未返回或不可用；显示已报告 token，不推算金额。':'用量来自接口返回；主 Agent 统计对话轮次，流水线统计请求次数，不推算金额。';
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
$('settings-form').onsubmit=async event=>{event.preventDefault();try{const config=await api('/api/config',{base_url:$('base-url').value,model:$('model').value,api_key:$('api-key').value,api_format:$('api-format').value});configView(config);$('api-key').value='';$('settings').close();notice();$('prompt').focus();}catch(error){$('settings-error').textContent=error.message;}};
$('new-chat').onclick=()=>createSession().catch(error=>notice(error.message));
$('versions').onchange=()=>{state.selectedRun=$('versions').value;renderRun();};
$('composer').onsubmit=async event=>{event.preventDefault();const message=$('prompt').value.trim();if(!message||state.snapshot?.busy)return;if(!state.config.configured){openSettings();return;}$('send').disabled=true;try{if(!state.sid)await createSession();await api(`/api/sessions/${state.sid}/messages`,{message});$('prompt').value='';notice();await refresh();}catch(error){notice(error.message);$('send').disabled=false;}};
$('prompt').onkeydown=event=>{if(event.key==='Enter'&&!event.shiftKey&&!event.isComposing){event.preventDefault();$('composer').requestSubmit();}};
for(const button of document.querySelectorAll('[data-prompt]'))button.onclick=()=>{$('prompt').value=button.dataset.prompt;$('prompt').focus();};
$('cancel-run').onclick=async()=>{try{await api(`/api/sessions/${state.sid}/runs/${state.selectedRun}/cancel`,{});await refresh();}catch(error){notice(error.message);}};
async function confirmPlan(action){try{await api(`/api/sessions/${state.sid}/confirm`,{plan_id:state.snapshot.pending_plan.id,action,target_rows:Number($('plan-target').value),max_pages:Number($('plan-budget').value)});notice();await refresh();}catch(error){notice(error.message);}}
$('confirm-plan').onclick=()=>confirmPlan('start');$('dismiss-plan').onclick=()=>confirmPlan('cancel');
async function resolveShortfall(action){try{await api(`/api/sessions/${state.sid}/runs/${state.selectedRun}/resolve`,{action,max_pages:Number($('extra-pages').value)});notice();await refresh();}catch(error){notice(error.message);}}
$('accept-shortfall').onclick=()=>resolveShortfall('accept');$('extend-shortfall').onclick=()=>resolveShortfall('extend');
$('adjust-scope').onclick=()=>{$('prompt').value='当前题数不足，我想调整主题或来源范围：';$('prompt').focus();};
async function poll(){await refresh();setTimeout(poll,2000);}
(async()=>{try{const data=await api('/api/bootstrap');state.token=data.token;configView(data.config);if(data.sessions.length)await selectSession(data.sessions[0].id);else await createSession();setTimeout(poll,2000);}catch(error){notice('无法连接工作空间：'+error.message);}})();
