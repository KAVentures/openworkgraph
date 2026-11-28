/* Read-only common-support intervention comparison. No source or business actions. */
(function(){
  'use strict';
  function el(tag,text){const n=document.createElement(tag);if(text!==undefined&&text!==null)n.textContent=String(text);return n;}
  function button(label,id){const n=el('button',label);n.type='button';n.className='secondary';if(id)n.id=id;return n;}
  function field(label,node){const n=el('label',label+' ');n.append(node);return n;}
  function input(id,type,value){const n=el('input');n.id=id;n.type=type;if(value!==undefined)n.value=value;return n;}
  function select(id,options){const n=el('select');n.id=id;for(const [v,label] of options){const o=el('option',label);o.value=v;n.append(o);}return n;}
  function grid(headers,rows){
    const wrap=el('div');wrap.className='table-wrap';wrap.style.maxWidth='100%';wrap.style.overflowX='auto';wrap.tabIndex=0;
    wrap.setAttribute('role','region');wrap.setAttribute('aria-label','Table: '+headers.join(', ').slice(0,120));
    const t=el('table'),head=el('thead'),hr=el('tr'),body=el('tbody');
    for(const h of headers){const th=el('th',h);th.scope='col';hr.append(th);}head.append(hr);
    for(const row of rows){const tr=el('tr');for(const v of row){const td=el('td');if(v instanceof Node)td.append(v);else td.textContent=String(v??'Not comparable');tr.append(td);}body.append(tr);}
    t.append(head,body);wrap.append(t);return wrap;
  }
  function save(value,name){const url=URL.createObjectURL(new Blob([JSON.stringify(value,null,2)],{type:'application/json'})),a=el('a');
    a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
  const scope=()=>({log:document.getElementById('owgEventLogPick')?.value||'',notion:document.getElementById('owgEventLogCaseType')?.value||''});
  const scopeKey=()=>JSON.stringify(scope());
  async function call(path,payload){
    await window.__owgAuthReady;
    const r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},cache:'no-store',body:JSON.stringify(payload)});
    const d=await r.json().catch(()=>({}));if(!r.ok)throw Error(typeof d.detail==='string'?d.detail:'Local case mix review refused.');return d;
  }
  function mount(){
    const anchor=document.getElementById('owgImpact');if(!anchor||anchor.querySelector('#owgImpactCaseMix'))return;
    const panel=el('details');panel.id='owgImpactCaseMix';panel.append(el('summary','Case mix and common-support intervention review'));
    const date=input('owgMixDate','date');
    const metric=select('owgMixMetric',[['cycle_time','Cycle time'],['rework','Repeated activity'],['sla_breach','SLA breach'],['activity_count','Activity count']]);
    const sla=input('owgMixSla','number');sla.min='1';sla.max='1000000000';sla.step='1';
    const activity=input('owgMixActivity','text');activity.maxLength=200;
    const slaField=field('SLA threshold seconds',sla),activityField=field('Exact activity to count',activity);
    const windowDays=input('owgMixWindow','number');windowDays.min='1';windowDays.max='3650';windowDays.step='1';
    const recent=input('owgMixRecent','number','0');recent.min='0';recent.max='3650';recent.step='1';
    const spanning=input('owgMixSpanning','checkbox');
    const dimension=select('owgMixDimension',[['first_activity','First recorded activity'],['first_resource','First recorded resource'],['utc_start_weekday','UTC start weekday']]);
    const minimum=input('owgMixMinimum','number','10');minimum.min='5';minimum.max='10000';minimum.step='1';
    const thresholds=input('owgMixThresholds','text','');thresholds.placeholder='Optional: 5,20,50';thresholds.maxLength=50;
    const treated=el('textarea');treated.id='owgMixTreated';treated.rows=3;treated.style.width='100%';treated.maxLength=8192;
    const control=el('textarea');control.id='owgMixControl';control.rows=3;control.style.width='100%';control.maxLength=8192;
    const filters=el('details');filters.append(el('summary','Optional treated and control cohort filters'),
      el('p','Use the existing exact case-filter JSON, for example {"resource":"Team A"}. Blank control means every other case. Filters can depend on later observed events.'),
      field('Treated cohort JSON',treated),field('Control cohort JSON',control));
    const prepare=button('Prepare exact case mix review','owgMixPrepare'),run=button('Run case mix review','owgMixRun'),download=button('Download case mix report','owgMixDownload');
    const prepared=el('textarea');prepared.id='owgMixPrepared';prepared.readOnly=true;prepared.rows=8;prepared.style.width='100%';
    const status=el('p');status.id='owgMixStatus';status.setAttribute('role','status');
    const out=el('div');out.id='owgMixOutput';const evidence=el('div');evidence.id='owgMixEvidence';
    let generation=0,ticket=0,request=null,report=null,selected=scopeKey();
    function invalidate(message='Review the exact current inputs again.'){
      generation++;ticket++;request=report=null;prepared.value='';out.replaceChildren();evidence.replaceChildren();
      run.disabled=download.disabled=true;status.textContent=message;
    }
    function syncMetric(){slaField.hidden=metric.value!=='sla_breach';activityField.hidden=metric.value!=='activity_count';}
    for(const n of [date,metric,sla,activity,windowDays,recent,spanning,dimension,minimum,thresholds,treated,control])n.addEventListener('input',()=>{syncMetric();invalidate();});
    function integer(n){if(n.value===''||!n.checkValidity()||!Number.isSafeInteger(Number(n.value)))throw Error('Enter all required whole-number settings.');return Number(n.value);}
    function cohort(n){if(!n.value.trim())return null;const v=JSON.parse(n.value);if(!v||typeof v!=='object'||Array.isArray(v))throw Error('A cohort must be a JSON object.');return v;}
    function active(n,key){return panel.isConnected&&n===generation&&key===scopeKey();}
    prepare.onclick=()=>{
      invalidate();selected=scopeKey();
      try{
        const s=scope();if(!s.log||!s.notion||!date.value)throw Error('Choose a retained log, case projection and UTC intervention date.');
        const analysis={intervention_at:date.value+'T00:00:00Z',metric:metric.value,case_type:s.notion,exclude_recent_days:integer(recent),exclude_spanning:spanning.checked};
        if(windowDays.value)analysis.window_days=integer(windowDays);
        if(metric.value==='sla_breach')analysis.sla_seconds=integer(sla);
        if(metric.value==='activity_count'){if(!activity.value.trim())throw Error('Enter the exact activity label.');analysis.activity=activity.value;}
        const a=cohort(treated),b=cohort(control);if(b&&!a)throw Error('A control cohort requires a treated cohort.');if(a)analysis.treated=a;if(b)analysis.control=b;
        const extra=thresholds.value.trim()?thresholds.value.split(',').map(x=>x.trim()):[];
        if(extra.length>7||extra.some(x=>!/^\d{1,5}$/.test(x)||Number(x)<5||Number(x)>10000)||new Set(extra.map(Number)).size!==extra.length)
          throw Error('Use up to seven distinct support thresholds from 5 to 10000.');
        const body={analysis,case_mix:{dimension:dimension.value,minimum_cases:integer(minimum),sensitivity_thresholds:extra.map(Number)}};
        request={log:s.log,body};prepared.value=JSON.stringify(request,null,2);run.disabled=false;
        status.textContent='Review the exact source, metric, exclusions and support threshold before running.';
      }catch(error){status.textContent=error instanceof SyntaxError?'Invalid cohort JSON.':error.message;}
    };
    const fmt=v=>v==null?'Not comparable':Number(v).toLocaleString(undefined,{maximumFractionDigits:6});
    const pct=v=>v==null?'Not comparable':(100*v).toFixed(2)+'%';
    const title=r=>r.label_kind==='observed'?r.label:r.label_kind+' start label';
    async function openEvidence(row,group,period,after=null){
      if(!report||!request||selected!==scopeKey())return;
      const n=generation,k=++ticket,key=scopeKey(),current=report,p=request;
      evidence.replaceChildren(el('p','Checking exact stratum evidence…'));
      try{
        const d=await call('/v1/event-logs/'+encodeURIComponent(p.log)+'/impact/case-mix/evidence',
          {...p.body,expected_review_sha256:current.review_sha256,stratum_sha256:row.stratum_sha256,group,period,after,limit:30});
        if(!active(n,key)||k!==ticket||report!==current)return;
        if(d.review_sha256!==current.review_sha256)throw Error('Unexpected review basis.');
        evidence.replaceChildren(el('h5','Exact case evidence: '+title(row)+' / '+group+' / '+period),
          el('p',d.selected_cases+' matching cases · '+d.cases.length+' shown. Values in '+d.unit+'.'),
          grid(['Case ID','Recorded start','Recorded end','Metric value'],d.cases.map(c=>[c.case_id,c.started_at,c.ended_at,fmt(c.value)])));
        const first=button('First stratum evidence page'),next=button('Next stratum evidence page'),exportPage=button('Download stratum evidence page');
        first.disabled=after===null;next.disabled=d.next_after===null;
        first.onclick=()=>openEvidence(row,group,period);next.onclick=()=>openEvidence(row,group,period,d.next_after);
        exportPage.onclick=()=>{if(active(n,key)&&k===ticket&&report===current)save(d,'owg-case-mix-evidence.json');};
        evidence.append(first,next,exportPage);
      }catch(error){if(active(n,key)&&k===ticket)evidence.replaceChildren(el('p',error.message));}
    }
    function render(data){
      const pop=data.population;out.replaceChildren(el('h4','Common-support and composition review'),
        el('p',data.common_support_strata+' / '+data.observed_strata+' strata meet the declared support rule in every selected cell. Metric unit: '+data.unit+'.'),
        grid(['Population','Cases'],Object.entries(pop).map(([k,v])=>[k.replaceAll('_',' '),v])),
        grid(['Group','Period','Included','Common support','Excluded from support','Coverage','Raw mean','Standardized mean'],
          Object.entries(data.groups).flatMap(([g,r])=>Object.entries(r.periods).map(([p,c])=>[g,p,c.included_cases,c.common_support_cases,c.excluded_from_common_support,pct(c.common_support_share),fmt(c.raw_mean),fmt(c.standardized_mean)]))),
        el('h4','Within-stratum and composition decomposition'),
        grid(['Group','Raw change','Common-support change','Within-stratum component','Composition component','Outside-support residual','Standardized change'],
          Object.entries(data.groups).map(([g,r])=>[g,...['raw_change','common_support_change','symmetric_within_stratum_component','symmetric_composition_component','raw_minus_common_support_residual','standardized_change'].map(k=>fmt(r[k]))])));
      if(data.control_adjusted)out.append(el('h4','Control-adjusted descriptive differences'),
        grid(['Measure','Value'],Object.entries(data.control_adjusted).map(([k,v])=>[k.replaceAll('_',' '),fmt(v)])));
      const sensitivity=el('details');sensitivity.id='owgMixSensitivity';
      sensitivity.append(el('summary','Reference population and support-threshold sensitivity'),el('p',data.sensitivity.interpretation));
      for(const row of data.sensitivity.thresholds){
        sensitivity.append(el('h5','Minimum '+row.minimum_cases+' cases per cell'+(row.primary_threshold?' (primary)':'')),
          el('p',row.common_support_strata+' common-support strata.'),
          grid(['Group','Period','Supported cases','Included cases','Coverage'],row.coverage.map(c=>[c.group,c.period,c.common_support_cases,c.included_cases,pct(c.share)])),
          grid(['Reference weights','Group','Baseline mean','Follow-up mean','Standardized change','Control-adjusted change'],
            row.reference_comparisons.flatMap(x=>Object.entries(x.groups).map(([g,c])=>[x.reference_basis,g,fmt(c.baseline_mean),fmt(c.follow_up_mean),fmt(c.standardized_change),fmt(x.standardized_difference_in_differences)]))),
          grid(['Group','Smallest change','Largest change','Strict direction agrees','All zero'],
            Object.entries(row.group_change_envelopes).map(([g,c])=>[g,fmt(c.minimum_change),fmt(c.maximum_change),c.strict_direction_agrees?'Yes':'No',c.all_zero?'Yes':'No'])));
      }
      out.append(sensitivity);
      const filter=select('owgMixStrataFilter',[['all','All strata'],['supported','Common support'],['excluded','Outside support']]);
      const search=input('owgMixStrataSearch','search');search.placeholder='Exact label search';const list=el('div');
      function draw(){
        const term=search.value.toLowerCase(),rows=data.strata.filter(r=>(filter.value==='all'||(filter.value==='supported')===r.common_support)&&title(r).toLowerCase().includes(term));
        list.replaceChildren(el('p',rows.length+' matching strata.'));
        for(const row of rows){
          const d=el('details');d.append(el('summary',title(row)+' · '+(row.common_support?'common support':row.exclusion_reason.replaceAll('_',' '))),
            el('p','Reference share: '+pct(row.common_reference_weight)));
          const items=Object.entries(row.cells).flatMap(([group,periods])=>Object.entries(periods).map(([period,cell])=>{
            const b=button('Review '+group+' '+period+' cases');b.disabled=!cell.cases;b.onclick=()=>openEvidence(row,group,period);
            return [group,period,cell.cases,fmt(cell.mean),b];}));
          d.append(grid(['Group','Period','Cases','Mean','Exact evidence'],items));list.append(d);
        }
      }
      filter.onchange=search.oninput=draw;draw();
      out.append(el('h4','Stratum support and exact evidence'),field('Stratum filter',filter),field('Search strata',search),list,el('p',data.interpretation),el('p','Review digest: '+data.review_sha256));
    }
    run.onclick=async()=>{
      if(!request)return;
      if(selected!==scopeKey()){selected=scopeKey();invalidate('Selected log or projection changed. Prepare again.');return;}
      const n=++generation,key=scopeKey(),p=request;ticket++;report=null;out.replaceChildren();evidence.replaceChildren();run.disabled=download.disabled=true;
      status.textContent='Computing bounded exact common-support review…';
      try{
        const data=await call('/v1/event-logs/'+encodeURIComponent(p.log)+'/impact/case-mix',p.body);
        if(!active(n,key))return;
        if(data.log_id!==p.log||data.analysis.case_type!==p.body.analysis.case_type)throw Error('Unexpected reviewed source.');
        report=data;render(data);download.disabled=false;status.textContent='Case mix review complete. No causal effect has been established.';
      }catch(error){if(active(n,key))status.textContent=error.message;}
      finally{if(active(n,key))run.disabled=false;}
    };
    download.onclick=()=>{if(report&&selected===scopeKey()&&panel.isConnected)save(report,'owg-intervention-case-mix.json');};
    const scopeChanged=()=>{if(panel.isConnected&&selected!==scopeKey()){selected=scopeKey();invalidate('Selected log or projection changed. Prepare again.');}};
    document.getElementById('owgEventLogPick')?.addEventListener('change',scopeChanged);
    document.getElementById('owgEventLogCaseType')?.addEventListener('change',scopeChanged);
    panel.append(el('p','Compare the same recorded case-start population across periods. Review support, composition and within-group changes without imputing missing groups.'),
      field('UTC intervention date',date),field('Metric',metric),slaField,activityField,field('Optional equal window days',windowDays),
      field('Exclude recent days',recent),field('Exclude spanning baseline cases',spanning),field('Start stratum',dimension),
      field('Minimum cases per cohort and period',minimum),field('Additional support thresholds, comma separated',thresholds),filters,prepare,field('Exact reviewed input',prepared),run,download,status,out,evidence);
    anchor.append(panel);syncMetric();invalidate('Select the source and review explicit settings.');
  }
  function start(){new MutationObserver(mount).observe(document.body,{childList:true,subtree:true});mount();}
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',start,{once:true});else start();
})();
