/* Independent signal history panel; same retained population as episode review. */
export function mount(host,{getJSON,focusRule,isCurrent}){
  const el=(tag,text)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=String(text);return n;};
  const button=text=>{const n=el('button',text);n.type='button';n.className='secondary';return n;};
  const field=(title,n)=>{const l=el('label');l.append(el('span',title),n);return l;};
  const input=(type,name)=>{const n=el('input');n.type=type;n.setAttribute('aria-label',name);return n;};
  const panel=el('details');panel.id='owgSignalRuleHistory';
  panel.append(el('summary','Rule-level deadline and recurrence review'),el('p','Review only retained evaluated transitions in an explicit completed UTC date window. Enter elapsed thresholds for recorded clearances and adjacent retriggers. These are descriptive review settings, not adopted service objectives or incident durations.'));
  const start=input('date','Rule history UTC start date'),end=input('date','Rule history exclusive UTC end date');
  const deadline=input('number','Recorded clearance deadline (seconds)'),recurrence=input('number','Retrigger horizon (seconds)');
  for(const n of [deadline,recurrence]){n.min='1';n.max='31622400';n.step='1';}
  const scope=input('text','Exact log identifier (optional)');scope.maxLength=128;
  const sort=el('select');sort.setAttribute('aria-label','Rule history order');
  for(const [key,name] of [['deadline','Late clearances and unresolved past threshold'],['recurrence','Adjacent retriggers within horizon'],['volume','Retained trigger count']]){const opt=el('option',name);opt.value=key;sort.append(opt);}
  const run=button('Review rule history'),prev=button('Previous rule history page'),next=button('Next rule history page'),save=button('Download rule history page JSON');
  run.id='owgSignalRuleHistoryRun';prev.id='owgSignalRuleHistoryPrevious';next.id='owgSignalRuleHistoryNext';save.id='owgSignalRuleHistoryDownload';
  const status=el('p');status.id='owgSignalRuleHistoryStatus';status.setAttribute('role','status');
  const result=el('div');result.id='owgSignalRuleHistoryResult';
  let generation=0,report=null,busy=false;
  const live=n=>n===generation&&host.isConnected&&isCurrent();
  const buttons=()=>{run.disabled=busy;prev.disabled=busy||!report||report.offset===0;next.disabled=busy||!report||report.next_offset===null;save.disabled=busy||!report;};
  const reset=()=>{generation++;busy=false;report=null;result.replaceChildren();status.textContent='Review thresholds and UTC window.';buttons();};
  for(const n of [start,end,deadline,recurrence,scope])n.addEventListener('input',reset);sort.onchange=reset;
  function integer(n){if(!n.value||!n.checkValidity()||!Number.isSafeInteger(Number(n.value)))throw Error('Enter both positive whole-second thresholds.');return n.value;}
  function table(name,head,rows){
    const wrapper=el('div');wrapper.style.overflowX='auto';wrapper.style.maxWidth='100%';
    const t=el('table');t.setAttribute('aria-label',name);const tr=el('tr');
    for(const h of head){const th=el('th',h);th.scope='col';tr.append(th);}
    const th=el('thead');th.append(tr);const b=el('tbody');
    for(const row of rows){const line=el('tr');row.forEach((v,i)=>{const c=el(i?'td':'th');if(!i)c.scope='row';if(v instanceof Node)c.append(v);else c.textContent=String(v??'None');line.append(c);});b.append(line);}
    t.append(th,b);wrapper.append(t);return wrapper;
  }
  const metrics=[['retained_triggers','Retained triggers'],['retained_clearances','Retained clearances'],['cleared_within_deadline','Cleared within threshold'],['cleared_after_deadline','Cleared after threshold'],['unresolved_window_past_deadline','Unresolved, window past threshold'],['unresolved_window_not_past_deadline','Unresolved, window not past threshold'],['interrupted_triggers','Interrupted triggers'],['ambiguous_triggers','Ambiguous triggers'],['ambiguous_clearances','Ambiguous clearances'],['leading_clearances','Clearances without retained trigger'],['observed_retrigger_pairs','Adjacent retrigger pairs'],['retrigger_within_horizon','Retriggers within horizon']];
  const dist=x=>x.count?x.count+' pairs; min '+x.minimum_us/1000000+' s; median '+x.median_us/1000000+' s; max '+x.maximum_us/1000000+' s':'No observed pairs';
  function render(r){
    result.replaceChildren(el('h5','Retained rule history'),el('p',r.matching_rule_count+' matching rules of '+r.all_rule_count+' with retained transitions · offset '+r.offset+' · exact review '+r.review_sha256),
      table('Complete retained window totals',['Measure','Matching rules','All rules'],metrics.map(([k,label])=>[label,r.matching_totals[k],r.all_totals[k]])),
      table('Rule-level retained evidence',['Log / rule',...metrics.map(x=>x[1]),'Observed clearance elapsed','Adjacent retrigger gap','Evidence'],r.rows.map(row=>{
        const focus=button('Inspect exact rule episodes');focus.setAttribute('aria-label','Inspect episodes for '+row.log_id+' '+row.rule_id);
        focus.onclick=()=>{if(report===r&&isCurrent())focusRule(row,r);};
        const name=el('div');name.append(el('strong',row.log_name+' · '+row.rule_name),el('p',row.log_id+' / '+row.rule_id));
        return [name,...metrics.map(([k])=>row[k]),dist(row.clearance_elapsed),dist(row.adjacent_retrigger_gap),focus];
      })),...r.interpretation.map(s=>el('p',s)));
  }
  async function load(offset=0,pin=null){
    const n=++generation;busy=true;report=null;result.replaceChildren();buttons();status.textContent='Reading bounded retained rule history…';
    try{
      if(!start.value||!end.value)throw Error('Choose both UTC dates; the end is exclusive.');
      const p=new URLSearchParams({start_date:start.value,end_date:end.value,deadline_seconds:integer(deadline),recurrence_seconds:integer(recurrence),sort:sort.value,offset:String(offset),limit:'30'});
      if(scope.value.trim())p.set('log_id',scope.value.trim());if(pin)p.set('expected_review_sha256',pin);
      const r=await getJSON('/v1/event-logs/signal-rule-history?'+p);
      if(!live(n))return;
      if(r.format!=='openworkgraph.signal-rule-history.v1'||r.actions_authorized!==false||!/^[a-f0-9]{64}$/.test(r.source_episode_review_sha256))throw Error('Unexpected rule history response.');
      report=r;render(r);status.textContent='Retained rule history reviewed. No live evaluation or notification occurred.';
    }catch(error){if(live(n)){report=null;result.replaceChildren();status.textContent=error.message;}}
    finally{if(live(n)){busy=false;buttons();}}
  }
  run.onclick=()=>load();prev.onclick=()=>{if(report)load(Math.max(0,report.offset-30),report.review_sha256);};next.onclick=()=>{if(report&&report.next_offset!==null)load(report.next_offset,report.review_sha256);};
  save.onclick=()=>{if(!report||save.disabled||!isCurrent())return;const url=URL.createObjectURL(new Blob([JSON.stringify(report,null,2)],{type:'application/json'})),a=el('a');a.href=url;a.download='owg-signal-rule-history-page.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
  panel.append(field('UTC start date',start),field('UTC end date (exclusive)',end),field('Recorded clearance threshold (seconds)',deadline),field('Adjacent retrigger horizon (seconds)',recurrence),field('Exact log ID (optional)',scope),field('Sort by retained evidence',sort),run,prev,next,save,status,result);host.replaceChildren(panel);reset();
}
