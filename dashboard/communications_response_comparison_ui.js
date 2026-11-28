/* No upload, persistence, network calls or HTML interpolation. */
(()=>{
  'use strict';
  const $=id=>document.getElementById(id);
  const el=(tag,value)=>{const n=document.createElement(tag);if(value!==undefined)n.textContent=String(value);return n;};
  const text=v=>v===null||v===undefined?'Unknown':typeof v==='number'?v.toLocaleString(undefined,{maximumFractionDigits:3}):String(v);
  const percent=v=>v===null?'Unknown':(v*100).toFixed(2)+'%';
  const signed=v=>(v>0?'+':'')+v.toFixed(2);
  const range=(l,u)=>percent(l)+' – '+percent(u);
  const hours=v=>v===null?'Unknown':text(v/3600)+' h';
  const humanDirection=v=>({higher_under_all_pending_outcomes:'Higher under every pending-outcome scenario',lower_under_all_pending_outcomes:'Lower under every pending-outcome scenario',overlapping_or_touching_bounds:'Overlapping or touching conditional bounds'})[v];
  function table(title,headers,rows){
    const wrap=el('div');wrap.className='table-wrap';const t=el('table');t.setAttribute('aria-label',title);
    const head=el('thead'),hr=el('tr');
    for(const h of headers){const th=el('th',h);th.scope='col';hr.append(th);}
    head.append(hr);const body=el('tbody');
    for(const row of rows){const tr=el('tr');row.forEach((v,i)=>{const cell=el(i?'td':'th',v);if(!i)cell.scope='row';tr.append(cell);});body.append(tr);}
    t.append(head,body);wrap.append(t);return wrap;
  }
  function deadlineTable(rows){
    return table('Deadline comparisons',['Deadline (minutes)','Baseline: conditional on-time share','Comparison: conditional on-time share','Change (percentage points)','Interpretation'],rows.map(d=>[
      d.deadline_minutes,range(d.baseline.lower_share,d.baseline.upper_share)+' ('+d.baseline.met+' observed met; '+d.baseline.pending+' pending / '+d.baseline.episodes+')',
      range(d.comparison.lower_share,d.comparison.upper_share)+' ('+d.comparison.met+' observed met; '+d.comparison.pending+' pending / '+d.comparison.episodes+')',
      signed(d.conditional_change_percentage_points.lower)+' to '+signed(d.conditional_change_percentage_points.upper),
      humanDirection(d.direction)
    ]));
  }
  function measureTable(a,b){
    const rows=[
      ['Eligible episodes',a.episodes,b.episodes],['Distinct threads',a.distinct_threads,b.distinct_threads],
      ['Observed replies',a.observed_replies,b.observed_replies],['Open episodes at cutoff',a.open_episodes,b.open_episodes],
      ['Repeat-contact episode share',percent(a.repeat_contact_share),percent(b.repeat_contact_share)],
      ['Follow-up episode share',percent(a.follow_up_share),percent(b.follow_up_share)],
      ['Inbound messages',a.inbound_messages,b.inbound_messages],
      ['Median observed reply time',hours(a.observed_reply_seconds.median),hours(b.observed_reply_seconds.median)],
      ['P90 observed reply time',hours(a.observed_reply_seconds.p90),hours(b.observed_reply_seconds.p90)],
      ['Median open age',hours(a.open_age_seconds.median),hours(b.open_age_seconds.median)],
      ['P90 open age',hours(a.open_age_seconds.p90),hours(b.open_age_seconds.p90)]
    ];
    if(a.workload&&b.workload)rows.push(
      ['Observed window duration',hours(a.workload.span_seconds),hours(b.workload.span_seconds)],
      ['Elapsed episode-wait exposure',hours(a.workload.episode_wait_seconds),hours(b.workload.episode_wait_seconds)],
      ['Peak observed backlog',a.workload.peak_observed_backlog,b.workload.peak_observed_backlog],
      ['Time-weighted observed backlog',text(a.workload.time_weighted_observed_backlog),text(b.workload.time_weighted_observed_backlog)]
    );
    return table('Descriptive measures',['Measure','Baseline','Comparison'],rows.map(row=>row.map(text)));
  }
  function groupSection(title,g){
    const box=el('section');box.append(el('h2',title),el('p',g.rows.length+' exact visible labels match. Baseline-only visible groups: '+g.baseline_visible_only+'; comparison-only: '+g.comparison_visible_only+'. Withheld or limited groups in source reports: '+g.baseline_withheld_groups+' / '+g.comparison_withheld_groups+'. Missing groups are not zero.'));
    for(const row of g.rows){
      const detail=el('details');detail.append(el('summary',row.label===null?'Mixed first-timestamp intent':row.label),measureTable(row.baseline,row.comparison),deadlineTable(row.deadlines));box.append(detail);
    }return box;
  }
  function render(r){
    const a=r.baseline,b=r.comparison,container=$('results');
    const basis=el('section');basis.append(el('h2','Coverage and exact inputs'),
      table('Report coverage',['Measure','Baseline','Comparison'],[
        ['Observation cutoff',a.cutoff,b.cutoff],['Threaded population',a.coverage.threaded,b.coverage.threaded],
        ['Eligible threads',a.coverage.eligible,b.coverage.eligible],['Excluded threads',a.coverage.excluded,b.coverage.excluded],
        ['Unthreaded messages',a.coverage.unthreaded_messages,b.coverage.unthreaded_messages],
        ['Eligible messages',a.coverage.eligible_messages,b.coverage.eligible_messages]].map(row=>row.map(text))));
    for(const [name,v] of [['Baseline',a],['Comparison',b]]){const p=el('p',name+' input SHA-256: ');p.append(el('code',v.input_sha256));basis.append(p);}
    basis.append(el('p','Identical deadline thresholds: '+r.common_deadline_minutes.join(', ')+' minutes. Baseline-only thresholds: '+(r.baseline_only_deadlines.join(', ')||'none')+'; comparison-only: '+(r.comparison_only_deadlines.join(', ')||'none')+'.'));
    container.replaceChildren(basis,el('h2','Conditional deadline performance'),deadlineTable(r.deadlines),el('h2','Observed response and workload measures'),
      measureTable(a.measures,b.measures),groupSection('Shared intent groups',r.by_intent),groupSection('Shared episode-kind groups',r.by_episode_kind),el('p',r.interpretation));
  }
  let generation=0,report=null,busy=false;
  function buttons(){
    $('compare').disabled=busy||!$('confirm').checked||!$('baseline').files.length||!$('comparison').files.length;
    $('download').disabled=busy||!report||!$('confirm').checked;
  }
  function invalidate(clearConsent=false){
    generation++;busy=false;report=null;$('results').replaceChildren();$('status').textContent='';
    if(clearConsent)$('confirm').checked=false;buttons();
  }
  async function read(file){
    if(!file||!file.size||file.size>OWGResponseComparison.MAX_BYTES)throw new Error('Choose a nonempty aggregate JSON file no larger than 8 MB.');
    if(!globalThis.crypto?.subtle)throw new Error('SHA-256 requires the local secure browser context.');
    const bytes=await file.arrayBuffer();
    if(bytes.byteLength>OWGResponseComparison.MAX_BYTES)throw new Error('Aggregate exceeds the size limit.');
    const sha256=[...new Uint8Array(await crypto.subtle.digest('SHA-256',bytes))].map(x=>x.toString(16).padStart(2,'0')).join('');
    let value;try{value=JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(bytes));}catch(_){throw new Error('Invalid UTF-8 aggregate JSON.');}
    return {value,sha256};
  }
  for(const id of ['baseline','comparison'])$(id).addEventListener('change',()=>invalidate(true));
  $('confirm').addEventListener('change',()=>invalidate());
  $('clear').addEventListener('click',()=>{$('baseline').value=$('comparison').value='';invalidate(true);});
  $('compare').addEventListener('click',async()=>{
    if($('compare').disabled)return;
    const run=++generation;busy=true;report=null;$('results').replaceChildren();$('status').textContent='Checking exact aggregate files…';buttons();
    try{
      const [a,b]=await Promise.all([read($('baseline').files[0]),read($('comparison').files[0])]);
      if(run!==generation||!$('confirm').checked)return;
      const next=OWGResponseComparison.compare(a.value,b.value,{baselineSha256:a.sha256,comparisonSha256:b.sha256,confirmComparable:true});
      render(next);report=next;$('status').textContent='Comparison ready. No files were uploaded.';
    }catch(error){if(run===generation){report=null;$('results').replaceChildren();$('status').textContent=error instanceof Error?error.message:'Comparison failed.';}}
    finally{if(run===generation){busy=false;buttons();}}
  });
  $('download').addEventListener('click',()=>{
    if($('download').disabled)return;
    const url=URL.createObjectURL(new Blob([JSON.stringify(report,null,2)],{type:'application/json'}));
    const a=el('a');a.href=url;a.download='owg-response-comparison.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  });
  buttons();
})();
