/* Local prospective calibration workbench. Frozen estimates are never modified. */
(function(){
  'use strict';
  function el(tag,text){const n=document.createElement(tag);if(text!==undefined&&text!==null)n.textContent=String(text);return n;}
  function button(label,id){const n=el('button',label);n.type='button';n.className='secondary';if(id)n.id=id;return n;}
  function field(label,node){const n=el('label',label+' ');n.append(node);return n;}
  function select(id,options){const n=el('select');n.id=id;for(const [v,label] of options){const o=el('option',label);o.value=v;n.append(o);}return n;}
  function grid(headers,rows){
    const wrap=el('div');wrap.className='table-wrap';wrap.style.maxWidth='100%';wrap.style.overflowX='auto';wrap.tabIndex=0;
    wrap.setAttribute('role','region');wrap.setAttribute('aria-label','Table: '+headers.join(', ').slice(0,120));
    const t=el('table'),h=el('thead'),hr=el('tr'),b=el('tbody');
    for(const x of headers){const th=el('th',x);th.scope='col';hr.append(th);}h.append(hr);
    for(const row of rows){const tr=el('tr');for(const x of row){const td=el('td');if(x instanceof Node)td.append(x);else td.textContent=String(x??'Not available');tr.append(td);}b.append(tr);}
    t.append(h,b);wrap.append(t);return wrap;
  }
  function save(value,name){const url=URL.createObjectURL(new Blob([JSON.stringify(value,null,2)],{type:'application/json'})),a=el('a');
    a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
  const logId=()=>document.getElementById('owgEventLogPick')?.value||'';
  async function api(path,body){
    await window.__owgAuthReady;
    const r=await fetch(path,{cache:'no-store',...(body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})});
    const d=await r.json().catch(()=>({}));if(!r.ok)throw Error(typeof d.detail==='string'?d.detail:'Local calibration review refused.');return d;
  }
  const pct=v=>v==null?'Not available':(100*v).toFixed(2)+'%';
  const bounds=(a,b)=>a==null||b==null?'Not available':pct(a)+' – '+pct(b);
  const title=g=>g.label??'Missing frozen label';
  const human=s=>s.replaceAll('_',' ');
  function mount(){
    const anchor=document.getElementById('owgEventLogPredictions');
    if(!anchor||anchor.querySelector('#owgCalibrationPanel'))return;
    const panel=el('details');panel.id='owgCalibrationPanel';
    const snap=select('owgCalibrationSnapshot',[['','Load and select a saved snapshot']]);
    const reload=button('Load calibration snapshots','owgCalibrationLoad');
    const bins=select('owgCalibrationBins',[['5','5 equal-width bins'],['10','10 equal-width bins'],['20','20 equal-width bins']]);bins.value='10';
    const grouping=select('owgCalibrationGroup',[['activity','Frozen last activity'],['basis','Frozen estimator basis'],['none','No subgroup breakdown']]);
    const minimum=el('input');minimum.id='owgCalibrationMinimum';minimum.type='number';minimum.min='5';minimum.max='5000';minimum.step='1';minimum.value='5';
    const run=button('Review frozen prediction calibration','owgCalibrationRun'),download=button('Download calibration report','owgCalibrationDownload');
    const status=el('p');status.id='owgCalibrationStatus';status.setAttribute('role','status');
    const out=el('div');out.id='owgCalibrationOutput';const evidence=el('div');evidence.id='owgCalibrationEvidence';
    let selected=logId(),generation=0,sequence=0,report=null,request=null;
    function invalidate(message='Review the selected snapshot and settings again.'){
      generation++;sequence++;report=request=null;out.replaceChildren();evidence.replaceChildren();
      download.disabled=true;run.disabled=!snap.value;status.textContent=message;
    }
    function active(n,id){return panel.isConnected&&n===generation&&id===logId()&&id===selected;}
    for(const n of [snap,bins,grouping,minimum])n.addEventListener('input',()=>invalidate());
    async function load(){
      selected=logId();snap.replaceChildren(new Option('Select a saved snapshot',''));invalidate('Loading exact saved snapshots…');
      const n=generation,id=selected;reload.disabled=true;
      try{
        if(!id)throw Error('Select and analyze a retained log first.');
        const d=await api('/v1/event-logs/'+encodeURIComponent(id)+'/prediction-snapshots');
        if(!active(n,id))return;
        if(d.log_id!==id)throw Error('Unexpected log identity.');
        for(const s of d.snapshots)snap.append(new Option(s.as_of+' · '+s.predicted_cases+' estimates · '+s.snapshot_id,s.snapshot_id));
        status.textContent=d.snapshots.length+' saved snapshots. Select one to review.';
      }catch(error){if(active(n,id))status.textContent=error.message;}
      finally{if(panel.isConnected)reload.disabled=false;}
    }
    reload.onclick=load;
    async function inspect(controls,offset=0){
      if(!report||!request||selected!==logId())return;
      const n=generation,k=++sequence,id=selected,p=request,r=report;
      evidence.replaceChildren(el('p','Rechecking exact calibration evidence…'));
      try{
        const d=await api(p.path+'/evidence',{settings:p.settings,evidence:{expected_review_sha256:r.review_sha256,...controls,offset,limit:25}});
        if(!active(n,id)||k!==sequence||report!==r)return;
        if(d.review_sha256!==r.review_sha256)throw Error('Unexpected review basis.');
        evidence.replaceChildren(el('h5','Exact frozen case evidence'),el('p',d.matching_cases+' matching cases; '+d.cases.length+' shown at offset '+offset+'.'),
          grid(['Case','Frozen activity','Frozen basis','Observation','Actual remaining seconds','Elapsed lower bound seconds','P10 / P50 / P90 seconds','Model risk','Baseline risk','Known outcome'],
            d.cases.map(c=>[c.case_id,c.last_activity,c.basis,c.observation_state,c.actual_remaining_seconds,c.elapsed_lower_bound_seconds,
              ['10','50','90'].map(q=>c.frozen_quantiles[q]??'—').join(' / '),pct(c.model_probability),pct(c.baseline_probability),c.observed_outcome])));
        const detail=el('details');detail.append(el('summary','Strict and inclusive quantile membership for each case'),
          grid(['Case','Quantile','Strictly below','At or below'],d.cases.flatMap(c=>Object.entries(c.quantile_membership).map(([q,v])=>[c.case_id,q,v?.[0],v?.[1]]))));
        const prev=button('Previous calibration evidence page'),next=button('Next calibration evidence page'),exportPage=button('Download calibration evidence page');
        prev.disabled=offset===0;next.disabled=d.next_offset===null;
        prev.onclick=()=>inspect(controls,Math.max(0,offset-25));next.onclick=()=>inspect(controls,d.next_offset);
        exportPage.onclick=()=>{if(active(n,id)&&sequence===k&&report===r)save(d,'owg-calibration-evidence.json');};
        evidence.append(detail,prev,next,exportPage);
      }catch(error){if(active(n,id)&&k===sequence)evidence.replaceChildren(el('p',error.message));}
    }
    function renderSummary(data,group){
      const section=el('div'),controls=group?{group_sha256:group.group_sha256}:{};
      const all=button('Review '+(group?title(group):'all frozen')+' cases');all.onclick=()=>inspect(controls);
      section.append(el('p',data.cases+' frozen cases'),grid(['Observation','Cases','Evidence'],Object.entries(data.observation_counts).map(([state,n])=>{
        const b=button('Review '+human(state)+' cases');b.disabled=!n;b.onclick=()=>inspect({...controls,state});return [human(state),n,b];})),all,
        el('h5','Tie-aware remaining-time quantile calibration'),
        grid(['Quantile target','Valid estimates','Strictly below bounds','At or below bounds','Diagnosis'],
          data.quantiles.map(q=>[q.quantile_percent+'%',q.valid_estimates,bounds(q.strictly_below.lower,q.strictly_below.upper),
            bounds(q.at_or_below.lower,q.at_or_below.upper),human(q.diagnosis)])));
      if(data.outcome_reliability){
        const r=data.outcome_reliability;
        section.append(el('h5','Paired outcome probability reliability'),el('p',r.paired_probability_cases+' valid paired forecasts; '+r.excluded_invalid_probability_pairs+' excluded invalid pairs.'),
          grid(['Forecast','Eventual fixed-bin ECE bounds','Cases in supported bins'],
            ['model','baseline'].map(arm=>[arm,bounds(r[arm].eventual_fixed_bin_ece_lower,r[arm].eventual_fixed_bin_ece_upper),r[arm].supported_bin_cases])));
        for(const arm of ['model','baseline']){
          const d=el('details');d.append(el('summary',human(arm)+' fixed probability bins'),
            grid(['Probability interval','Cases','Known positive','Known negative','Unknown','Mean risk','Eventual observed rate','Absolute gap bounds','Diagnosis','Evidence'],
              r[arm].bins.map(b=>{
                const btn=button('Review '+arm+' bin '+(b.bin_index+1));btn.disabled=!b.cases;btn.onclick=()=>inspect({...controls,arm,bin_index:b.bin_index});
                return [pct(b.lower_probability)+' to '+pct(b.upper_probability)+(b.upper_inclusive?' inclusive':' exclusive'),b.cases,b.known_positive,b.known_negative,b.unknown,
                  pct(b.mean_predicted),bounds(b.eventual_observed_rate_lower,b.eventual_observed_rate_upper),bounds(b.absolute_calibration_gap_lower,b.absolute_calibration_gap_upper),human(b.diagnosis),btn];})));
          section.append(d);
        }
        section.append(el('p',r.interpretation));
      }
      return section;
    }
    function render(data){
      out.replaceChildren(el('h4','Prospective calibration and unresolved-outcome bounds'),el('p','Frozen at '+data.snapshot_as_of+'; evaluated against '+(data.evaluated_against??'unknown horizon')+'.'),
        renderSummary(data.overall,null),el('h4','Frozen subgroup calibration'),
        el('p',data.groups_withheld_or_limited+' groups withheld or limited, containing '+data.cases_in_withheld_or_limited_groups+' cases. Minimum '+data.minimum_group_cases+' cases per displayed subgroup.'));
      const search=el('input');search.type='search';search.id='owgCalibrationGroupSearch';const list=el('div');
      function draw(){
        const term=search.value.toLowerCase();list.replaceChildren();
        for(const g of data.groups.filter(g=>title(g).toLowerCase().includes(term))){
          const d=el('details');d.append(el('summary',title(g)+' · '+g.cases+' cases'),renderSummary(g,g));list.append(d);
        }
      }
      search.oninput=draw;draw();
      out.append(field('Search frozen groups',search),list,el('p',data.interpretation),el('p','Review digest: '+data.review_sha256));
    }
    run.onclick=async()=>{
      if(selected!==logId()||!snap.value){invalidate('Reload saved snapshots for the selected log.');return;}
      invalidate('Reviewing the exact frozen population…');
      if(!minimum.value||!minimum.checkValidity()||!Number.isSafeInteger(Number(minimum.value))){status.textContent='Choose an integer minimum from 5 to 5000.';return;}
      const id=selected,n=generation,sid=snap.value,p={path:'/v1/event-logs/'+encodeURIComponent(id)+'/prediction-snapshots/'+encodeURIComponent(sid)+'/calibration',
        settings:{bins:Number(bins.value),minimum_cases:Number(minimum.value),group_by:grouping.value}};
      request=p;run.disabled=true;
      try{
        const d=await api(p.path,{settings:p.settings});
        if(!active(n,id))return;
        if(d.log_id!==id||d.snapshot_id!==sid)throw Error('Unexpected frozen source.');
        report=d;render(d);download.disabled=false;status.textContent='Calibration review complete. No forecasts were modified.';
      }catch(error){if(active(n,id))status.textContent=error.message;}
      finally{if(active(n,id))run.disabled=false;}
    };
    download.onclick=()=>{if(report&&selected===logId()&&panel.isConnected)save(report,'owg-prospective-calibration.json');};
    document.getElementById('owgEventLogPick')?.addEventListener('change',()=>{
      if(!panel.isConnected||selected===logId())return;
      selected=logId();snap.replaceChildren(new Option('Load and select a saved snapshot',''));invalidate('Selected log changed; load snapshots again.');
    });
    panel.append(el('summary','Prospective calibration and frozen case evidence'),
      el('p','Inspect the frozen prediction population without assuming unfinished cases are complete. No recalibration or execution is performed.'),
      reload,field('Saved prediction snapshot',snap),field('Outcome probability binning',bins),field('Frozen grouping dimension',grouping),
      field('Minimum subgroup and probability-bin cases',minimum),run,download,status,out,evidence);
    anchor.append(panel);invalidate('Load saved snapshots to begin.');
  }
  function start(){new MutationObserver(mount).observe(document.body,{childList:true,subtree:true});mount();}
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',start,{once:true});else start();
})();
