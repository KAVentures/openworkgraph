/* Local-only process mining: no default upload, retention or external service. */
(() => {
  'use strict';
  const SVG_NS='http://www.w3.org/2000/svg';
  const MAX_UPLOAD=1024*1024;
  let report=null;
  let activeVariant=0;
  const make=(tag,text,className)=>{
    const el=document.createElement(tag);
    if(text!==undefined&&text!==null)el.textContent=String(text);
    if(className)el.className=className;
    return el;
  };
  const safe=(v)=>Array.isArray(v)?v:[];
  const seconds=(s)=>{
    const n=Number(s||0);
    if(n>=3600)return (n/3600).toFixed(1)+' h';
    if(n>=60)return (n/60).toFixed(1)+' min';
    return n.toFixed(1)+' s';
  };
  const svg=(tag,attrs)=>{
    const node=document.createElementNS(SVG_NS,tag);
    for(const [key,value] of Object.entries(attrs||{}))node.setAttribute(key,String(value));
    return node;
  };
  const replace=(host,...children)=>{host.replaceChildren(...children);};
  const button=(title,fn)=>{
    const b=make('button',title,'secondary');b.type='button';b.addEventListener('click',fn);return b;
  };
  function install(){
    const panel=document.getElementById('panel-evidence');
    if(!panel||document.getElementById('owgProcessCSV'))return;
    const card=make('section',null,'card');card.id='owgProcessCSV';
    card.appendChild(make('h2','Process mining · CSV event logs'));
    card.appendChild(make('p','Import a small event log to inspect complete case paths, transitions, elapsed durations and repeated activities. This is a one-time local preview; nothing is saved or sent to an AI.','muted'));
    const p=make('p','CSV header: case_id,activity,timestamp (optional source). Timestamps must include a UTC offset, e.g. 2026-10-20T10:00:00Z. Only use files you are authorized to analyze. Case IDs are replaced with temporary labels; activity names are displayed as supplied.','muted');
    card.appendChild(p);
    const wrap=make('div',null,'owg-process-controls');
    const file=make('input');file.type='file';file.accept='.csv,text/csv';file.id='owgProcessFile';file.setAttribute('aria-label','Choose business event log CSV');
    const run=button('Analyze selected CSV',analyze);run.id='owgProcessAnalyze';run.disabled=true;
    file.addEventListener('change',()=>{
      if(window.owgProcessDesigner?.hasDraft?.() && !window.confirm(
        'Discard your unsaved proposed process before selecting a different CSV?')) {
        file.value='';
        run.disabled=true;
        return;
      }
      report=null;
      run.disabled=!file.files?.length;
      clearReport();
    });
    wrap.append(file,run);card.appendChild(wrap);
    const status=make('p','No CSV selected. This feature is opt-in and does not change desktop recording.','muted');
    status.id='owgProcessStatus';status.setAttribute('role','status');card.appendChild(status);
    const output=make('div');output.id='owgProcessResults';card.appendChild(output);
    const style=make('style');style.textContent=[
      '#owgProcessCSV .owg-process-controls{display:flex;flex-wrap:wrap;gap:10px;align-items:center;margin:12px 0}',
      '#owgProcessCSV .owg-pmetrics{display:flex;flex-wrap:wrap;gap:10px;margin:12px 0}',
      '#owgProcessCSV .owg-pmetric{border:1px solid #d9dfd4;border-radius:8px;padding:10px;min-width:130px;flex:1}',
      '#owgProcessCSV .owg-pmetric strong{display:block;font-size:21px}',
      '#owgProcessCSV .owg-variants{display:flex;flex-wrap:wrap;gap:8px;margin:10px 0}',
      '#owgProcessCSV .owg-variants button[aria-pressed=true]{outline:2px solid #3b7462}',
      '#owgProcessCSV .owg-process-map{overflow:auto;max-width:100%;border:1px solid #d9dfd4;border-radius:8px;margin:8px 0}',
      '#owgProcessCSV .owg-process-map svg{display:block;max-height:110px}',
      '#owgProcessCSV .owg-process-table{width:100%;overflow:auto}',
      '#owgProcessCSV .owg-process-table table{min-width:300px;width:100%}'
    ].join('\n');
    card.prepend(style);
    const browse=panel.querySelector('#savedDayCard');
    if(browse)browse.insertAdjacentElement('afterend',card);else panel.appendChild(card);
  }
  function clearReport(){
    const host=document.getElementById('owgProcessResults');if(host)host.replaceChildren();
    window.owgProcessDesigner?.reset?.();
    window.owgOpportunityReview?.reset?.();
  }
  function setStatus(message){
    const s=document.getElementById('owgProcessStatus');if(s)s.textContent=String(message||'');
  }
  async function analyze(){
    const file=document.getElementById('owgProcessFile')?.files?.[0];
    const control=document.getElementById('owgProcessAnalyze');
    if(!file)return;
    if(file.size>MAX_UPLOAD){setStatus('File exceeds the 1 MiB upload limit.');return;}
    report=null;clearReport();control.disabled=true;setStatus('Analyzing selected CSV on this local OWG service…');
    try{
      await window.__owgAuthReady;
      const response=await fetch('/v1/process-mining/preview',{
        method:'POST',headers:{'Content-Type':'text/csv'},body:file,cache:'no-store'
      });
      if(!response.ok){
        const error=await response.json().catch(()=>({}));
        throw new Error(String(error?.detail||('Local import failed ('+response.status+')')));
      }
      const result=await response.json();
      if(result.format!=='openworkgraph.process-mining-csv-preview.v1')
        throw new Error('Unexpected process-mining response.');
      report=result;activeVariant=0;render();
      setStatus('Local analysis complete. Case labels are temporary; nothing was stored in OWG. Refresh or leave this page to discard this preview.');
    }catch(error){
      setStatus('Unable to analyze: '+String(error?.message||'Unknown error'));
    }finally{control.disabled=false;}
  }
  function metric(label,value){
    const host=make('div',null,'owg-pmetric');
    host.append(make('strong',value),make('span',label));return host;
  }
  function graph(path){
    const scroller=make('div',null,'owg-process-map');
    scroller.setAttribute('aria-label','Observed event sequence; the arrows represent recorded order, not causal dependencies');
    const steps=safe(path).slice(0,24), width=Math.max(300,steps.length*184+35);
    const drawing=svg('svg',{role:'img',width,height:96,viewBox:'0 0 '+width+' 96',
      'aria-label':'Observed events in order, not a confirmed workflow'});
    steps.forEach((step,i)=>{
      const x=12+i*184;
      if(i){
        const link=svg('path',{d:'M '+(x-19)+' 43 H '+(x-5),stroke:'#607c71','stroke-width':2,fill:'none'});
        drawing.appendChild(link);
        const tip=svg('path',{d:'M '+(x-10)+' 38 L '+(x-5)+' 43 L '+(x-10)+' 48',
          stroke:'#607c71','stroke-width':2,fill:'none'});drawing.appendChild(tip);
      }
      const rect=svg('rect',{x,y:13,width:161,height:61,rx:9,stroke:'#557e70',fill:'#f1f7f4'});
      const name=svg('text',{x:x+9,y:39,fill:'#234236','font-size':12});
      const value=String(step);
      name.textContent=value.length>22?value.slice(0,21)+'…':value;
      const stepNo=svg('text',{x:x+9,y:57,fill:'#546e62','font-size':10});
      stepNo.textContent='Event '+(i+1);
      const title=svg('title');title.textContent=value;
      drawing.append(rect,name,stepNo,title);
    });
    scroller.appendChild(drawing);return scroller;
  }
  function render(){
    const host=document.getElementById('owgProcessResults');if(!host||!report)return;
    host.replaceChildren();
    const stats=make('div',null,'owg-pmetrics');
    stats.append(metric('Cases',report.case_count),metric('Events',report.input_rows),
      metric('Activities',report.activity_count),metric('Observed variants',report.variant_count),
      metric('Median case elapsed',seconds(report.metrics?.median_case_elapsed_seconds)),
      metric('Median inter-event gap',report.metrics?.median_inter_event_gap_seconds==null?
        '—':seconds(report.metrics.median_inter_event_gap_seconds)));
    host.appendChild(stats);
    host.appendChild(make('p','Elapsed time spans first to last logged event; gaps are not proven waiting, and repeated activities are not necessarily rework. No case outcome or automation authority is established.','muted'));
    host.appendChild(make('h3','Observed process variants'));
    const variants=safe(report.variants);
    const controls=make('div',null,'owg-variants');
    const visible=variants.slice(0,10);
    visible.forEach((item,index)=>{
      const b=button('Variant '+(index+1)+' · '+item.case_count+' cases',()=>{
        activeVariant=index;render();
      });
      b.setAttribute('aria-pressed',String(activeVariant===index));controls.appendChild(b);
    });
    host.appendChild(controls);
    if(variants.length){
      const selected=variants[Math.min(activeVariant,variants.length-1)];
      const edit=button('Design improved TO-BE workflow',()=>{
        if(!window.owgProcessDesigner?.open) {
          setStatus('Process designer unavailable. Reopen the local dashboard.');
          return;
        }
        window.owgProcessDesigner.open(report,selected);
      });
      edit.disabled=!!selected.path_truncated;
      if(selected.path_truncated)edit.title='This observed path is truncated; design requires a complete observed variant.';
      host.appendChild(edit);
    }
    if(variants.length){
      const variant=variants[Math.min(activeVariant,variants.length-1)];
      host.appendChild(graph(variant.observed_path));
      host.appendChild(make('p',variant.path_truncated?
        'This variant contains '+variant.path_event_count+' events. Only the first 24 are shown.':
        'Each arrow means next recorded activity in the same supplied case, not a causal or required step.','muted'));
    }
    if(report.variants_truncated)host.appendChild(make('p','Additional variants are omitted from this bounded preview.','muted'));
    host.appendChild(make('h3','Observed transitions'));
    const wrapper=make('div',null,'owg-process-table');
    const table=make('table'),head=make('thead'),hr=make('tr');
    ['From','To','Transitions','Cases'].forEach(t=>hr.appendChild(make('th',t)));head.appendChild(hr);
    const body=make('tbody');
    safe(report.transitions).slice(0,20).forEach(edge=>{
      const row=make('tr');
      [edge.from,edge.to,edge.transition_count,edge.case_count].forEach(v=>row.appendChild(make('td',v)));
      body.appendChild(row);
    });
    table.append(head,body);wrapper.appendChild(table);host.appendChild(wrapper);
    host.appendChild(make('p','Case IDs and source values are never returned; the CSV preview is not saved. Process mining from live business-system connectors is a separate, future capability.','muted'));
    window.owgOpportunityReview?.render?.(report);
  }
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',install);else install();
})();